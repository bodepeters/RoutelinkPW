import json
import urllib.request
import uuid

boundary = '----pwtest' + uuid.uuid4().hex
parts = []
for name, filename in [('baseline', 'sample_baseline.csv'), ('current', 'sample_current.csv')]:
    data = open('data/' + filename, 'rb').read()
    header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'.encode()
    parts.append(header + data + b'\r\n')
body = b''.join(parts) + f'--{boundary}--\r\n'.encode()
request = urllib.request.Request(
    'http://127.0.0.1:8765/api/partners/1/scan',
    data=body,
    headers={'Content-Type': 'multipart/form-data; boundary=' + boundary},
    method='POST',
)
result = json.load(urllib.request.urlopen(request))
print(json.dumps(result, indent=2))
assert result['summary']['total_changes'] == 4
assert result['summary']['price_increases'] == 2
assert result['summary']['price_decreases'] == 1
assert result['summary']['billing_changes'] == 1
print('SMOKE TEST PASSED')
