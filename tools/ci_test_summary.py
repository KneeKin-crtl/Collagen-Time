"""Expose failed CI tests in the run summary when API log access is unavailable."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

report = Path('test-results.xml')
summary = os.environ.get('GITHUB_STEP_SUMMARY')
if report.exists() and summary:
    with Path(summary).open('a', encoding='utf-8') as output:
        output.write('### Failed Python checks\n\n')
        for test in ET.parse(report).iter('testcase'):
            for failure in list(test.findall('failure')) + list(test.findall('error')):
                output.write(f"#### {test.attrib.get('classname')}.{test.attrib.get('name')}\n\n")
                output.write('```text\n' + (failure.text or failure.attrib.get('message', ''))[-12000:] + '\n```\n\n')
