import unittest
from concurrent.futures import CancelledError
from native.jobs import Job


class JobCancellationTests(unittest.TestCase):
    def collect(self,job):
        outcomes=[]
        job.signals.result.connect(lambda value:outcomes.append(('result',value)))
        job.signals.cancelled.connect(lambda:outcomes.append(('cancelled',None)))
        job.signals.finished.connect(lambda:outcomes.append(('finished',None)))
        job.run();return outcomes
    def test_saved_pause_result_is_delivered_after_cancellation_request(self):
        job=Job(lambda progress,cancel:dict(status='paused',checkpoint_s=3),result_on_cancel=True);job.cancel()
        self.assertEqual(self.collect(job),[('result',dict(status='paused',checkpoint_s=3)),('finished',None)])
    def test_other_jobs_keep_discarding_cancelled_results(self):
        job=Job(lambda progress,cancel:'discard');job.cancel()
        self.assertEqual(self.collect(job),[('cancelled',None),('finished',None)])
    def test_preparation_cancel_does_not_publish_nonexistent_checkpoint(self):
        def prepare(progress,cancel):raise CancelledError()
        job=Job(prepare,result_on_cancel=True);self.assertEqual(self.collect(job),[('cancelled',None),('finished',None)])
