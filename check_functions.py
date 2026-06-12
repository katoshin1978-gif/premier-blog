#!/usr/bin/env python3
import zipfile, io, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with zipfile.ZipFile('C:/premier-blog/premier-blog-theme.zip', 'r') as z:
    content = z.read('functions.php').decode('utf-8')

lines = content.split('\n')
for i, line in enumerate(lines):
    if any(kw in line.lower() for kw in ['set-option', 'set_option', 'register_rest_route', 'rest_api_init']):
        start = max(0, i-2)
        end = min(len(lines), i+20)
        print(f'--- Line {i+1} ---')
        for j in range(start, end):
            print(f'{j+1}: {lines[j]}')
        print()
