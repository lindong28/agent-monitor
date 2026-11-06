"""Temporary acceptance-branch adapter; executes the existing service log tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
suite = unittest.defaultTestLoader.discover('tests', pattern='test_service_logs.py')
collected = suite.countTestCases()
class Result(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        self.failed_cases = getattr(self, 'failed_cases', set())
        self.error_cases = getattr(self, 'error_cases', set())
    def addFailure(self, test, err):
        self.failed_cases.add(test.id()); super().addFailure(test, err)
    def addError(self, test, err):
        self.error_cases.add(test.id()); super().addError(test, err)
    def addSubTest(self, test, subtest, err):
        if err is not None:
            target = self.failed_cases if issubclass(err[0], test.failureException) else self.error_cases
            target.add(test.id())
        super().addSubTest(test, subtest, err)
with (out/'tests.log').open('w') as log:
    result = unittest.TextTestRunner(stream=log, resultclass=Result).run(suite)
# Each collected method counts once; errors take precedence over assertions.
failed = len(result.failed_cases - result.error_cases)
errors = len(result.error_cases)
report = {'schema_version': 1, 'report_id': os.environ['REPO_CI_REPORT_ID'],
          'revision': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
          'dirty': bool(subprocess.check_output(['git','status','--porcelain'],text=True)),
          'profile':'service-logs', 'status':'passed' if result.wasSuccessful() else 'failed', 'phase':'tests',
          'counts':{'collected':collected,'passed':result.testsRun-failed-errors-len(result.skipped),'failed':failed,
                    'errors':errors,'skipped':len(result.skipped),'not_run':collected-result.testsRun},
          'failed_test_ids':sorted(result.failed_cases | result.error_cases)}
(out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
raise SystemExit(0 if result.wasSuccessful() else 1)
