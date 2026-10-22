"""Small numeric expression language; no Python execution or hidden model state."""
import ast
import math
from functools import lru_cache

OBSERVABLES = {
    'reported_processed_s', 'reported_cpu_ms', 'witness_messages_s',
    'report_missing', 'report_age_s', 'report_rtt_ms',
    'zone_median_processed_s', 'zone_mad_processed_s', 'zone_coverage',
    'temporal_delta_processed_s', 'temporal_mean_processed_s',
    'reported_operation_entropy', 'reported_peer_entropy', 'reported_io_ratio',
}
FUNCTIONS = {'abs': abs, 'min': min, 'max': max, 'sqrt': math.sqrt,
             'log1p': math.log1p, 'safe_div': lambda a,b: a/b if b else 0.}


@lru_cache(maxsize=512)
def parse(expression):
    if not isinstance(expression,str) or len(expression)>500:raise ValueError('Формула: максимум 500 символов')
    try:tree=ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError) as error:raise ValueError('Некорректная формула') from error
    if len(list(ast.walk(tree)))>100:raise ValueError('Слишком сложная формула')
    def check(node,depth=0):
        if depth>16:raise ValueError('Слишком глубокая формула')
        if isinstance(node,ast.Expression):check(node.body,depth+1)
        elif isinstance(node,ast.Constant):
            if type(node.value) not in (int,float) or not math.isfinite(node.value) or abs(node.value)>1e9:raise ValueError('Недопустимая константа')
        elif isinstance(node,ast.Name):
            if node.id not in OBSERVABLES:raise ValueError('Неизвестное наблюдение: '+node.id)
        elif isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            check(node.left,depth+1);check(node.right,depth+1)
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):check(node.operand,depth+1)
        elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in FUNCTIONS and not node.keywords and 1<=len(node.args)<=4:
            for arg in node.args:check(arg,depth+1)
        else:raise ValueError('Допустимы наблюдения, числа, + − × / и числовые функции')
    check(tree)
    return tree.body


def evaluate(expression,values):
    def calc(node):
        if isinstance(node,ast.Constant):return node.value
        if isinstance(node,ast.Name):return values[node.id]
        if isinstance(node,ast.UnaryOp):return (-1 if isinstance(node.op,ast.USub) else 1)*calc(node.operand)
        if isinstance(node,ast.BinOp):
            a,b=calc(node.left),calc(node.right)
            if isinstance(node.op,ast.Add):return a+b
            if isinstance(node.op,ast.Sub):return a-b
            if isinstance(node.op,ast.Mult):return a*b
            return a/b
        return FUNCTIONS[node.func.id](*(calc(arg) for arg in node.args))
    try:value=float(calc(parse(expression)))
    except (ArithmeticError,ValueError,TypeError) as error:raise ValueError('Ошибка вычисления формулы: '+expression) from error
    if not math.isfinite(value) or abs(value)>1e15:raise ValueError('Формула дала недопустимое значение')
    return value
