import zipfile
import os

files_to_read = [
    'front-page.php',
    'template-parts/hero-asym.php',
    'template-parts/card.php',
    'template-parts/card-row.php',
]

os.makedirs('C:/tmp_theme', exist_ok=True)

with zipfile.ZipFile('C:/premier-blog/premier-blog-theme.zip', 'r') as z:
    for f in files_to_read:
        content = z.read(f).decode('utf-8')
        safe_name = f.replace('/', '_')
        out_path = f'C:/tmp_theme/{safe_name}'
        with open(out_path, 'w', encoding='utf-8') as out:
            out.write(content)
        print(f'Written: {out_path}, size={len(content)}')
