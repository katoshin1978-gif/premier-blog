"""
functions.php をFTP経由でテーマディレクトリへ直接デプロイするスクリプト。
事前に .env に FTP_HOST / FTP_USERNAME / FTP_PASSWORD / FTP_PORT が必要。
"""
import os
import ftplib
from dotenv import load_dotenv

load_dotenv("C:/premier-blog/.env")

FTP_HOST = os.environ["FTP_HOST"]
FTP_PORT = int(os.environ.get("FTP_PORT", 21))
FTP_USERNAME = os.environ["FTP_USERNAME"]
FTP_PASSWORD = os.environ["FTP_PASSWORD"]
REMOTE_DIR = "premier-blog.com/public_html/wp-content/themes/premier-blog-theme-1"
LOCAL_PATH = "wordpress/Premier-blog/premier-blog-theme/functions.php"

with open(LOCAL_PATH, "rb") as f:
    content = f.read()
print(f"functions.php: {len(content):,} bytes")

ftp = ftplib.FTP()
ftp.connect(FTP_HOST, FTP_PORT, timeout=20)
ftp.login(FTP_USERNAME, FTP_PASSWORD)
ftp.cwd(REMOTE_DIR)

with open(LOCAL_PATH, "rb") as f:
    ftp.storbinary("STOR functions.php", f)
print("アップロード完了")

# アップロード後に再取得して差分ゼロを確認
verify_path = "functions.php.uploaded_check"
with open(verify_path, "wb") as f:
    ftp.retrbinary("RETR functions.php", f.write)
ftp.quit()

with open(verify_path, "rb") as f:
    remote_content = f.read()
os.remove(verify_path)

if remote_content == content:
    print("検証OK: サーバー側の内容がローカルと完全一致")
else:
    print("警告: アップロード後の内容がローカルと一致しません。手動確認してください")
