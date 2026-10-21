"""Shared, dependency-free colors for the main UI and early startup card."""
EDITOR_PALETTES={
    'graphite':dict(name='Тёмная · Графит',bg='#1e1e1e',sidebar='#252526',panel='#252526',
        raised='#333333',input='#3c3c3c',hover='#37373d',border='#454545',grid='#303031',
        text='#d4d4d4',muted='#a6a6a6',disabled='#808080',accent='#007acc',
        accent_text='#4fc1ff',accent_hover='#1177bb',on_accent='#ffffff',
        selection='#094771',selection_text='#ffffff',blue='#569cd6',teal='#4ec9b0',
        gold='#d7ba7d',orange='#ce9178',red='#f48771'),
    'porcelain':dict(name='Светлая · Фарфор',bg='#ffffff',sidebar='#f3f3f3',panel='#f8f8f8',
        raised='#e8e8e8',input='#ffffff',hover='#e4e6f1',border='#cecece',grid='#ededed',
        text='#333333',muted='#616161',disabled='#767676',accent='#007acc',
        accent_text='#005fb8',accent_hover='#006bb3',on_accent='#ffffff',
        selection='#add6ff',selection_text='#333333',blue='#0451a5',teal='#267f74',
        gold='#795e26',orange='#a31515',red='#cd3131'),
}
PALETTE_LABELS={
    'bg':'Рабочая область','sidebar':'Боковое меню','panel':'Панели','raised':'Кнопки',
    'input':'Поля ввода','hover':'Наведение','border':'Границы','grid':'Сетка',
    'text':'Основной текст','muted':'Вторичный текст','disabled':'Недоступные элементы',
    'accent':'Основной акцент','accent_text':'Акцентный текст','accent_hover':'Акцент при наведении',
    'on_accent':'Текст на акценте','selection':'Выделение','selection_text':'Текст выделения',
    'blue':'Протоколы · синий','teal':'Протоколы · бирюзовый','gold':'Протоколы · золотой',
    'orange':'Предупреждения','red':'Ошибки',
}
LEGACY_ROLES={}
for role,colors in {
    'bg':'11151c 141c27',
    'sidebar':'151a23',
    'panel':'191f29 171e28 1b2430 18222f 202c3b 1d2734 202a38 263447',
    'input':'131a24',
    'raised':'212a36 232c39 2d3e4c 273342',
    'hover':'202936 283444 2b3747 344354',
    'border':'2c3746 2a3443 28313e 344153 344255 2c3849 3a4c60 344659 3b4d63 3a4b60 536980 50637a 4b617c 2e3c4e 293342 39495b 456052',
    'grid':'243245 293546 2a3544',
    'selection':'213a35 263b38 2c493f 294c43 34564d 2e6e60 356354',
    'selection_text':'a6efd1 d4f7e9 c6f4e3',
    'accent_text':'8fe0c2 9ce8c9 a4eed3 a6edd4 79bfa7 82cdb3',
    'on_accent':'102b23 163c2c',
    'text':'e5ebf5 f0f3fa e7eef9 e3edf8 d9e3f3 cfdaeb d6e4f7 e1eaf7 d8e9f4 aebed1 d3e3f3 e4edf9 a9cfbd',
    'muted':'8c9bb0 6f8198 62778d 668f85 7389a3 536c87 849bb4 9eb2cc 8f9db1 a7b7cb 94a5bd 7f92ab 96a8c0 8195af 94acc6 9cacc1 a2b4cb 667c94 71849e 74879e',
    'disabled':'566376 627084',
    'blue':'82adfa 7fa8ea a5baff',
    'teal':'82c9ae 80cdaa 80c6b0',
    'gold':'d5aa75 d9b27b',
    'orange':'e7ad81 e7b375',
    'red':'ed9c9f ed8c93',
}.items():
    LEGACY_ROLES.update(('#'+value,role) for value in colors.split())
