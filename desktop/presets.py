import base64


def message_command(text: str) -> dict:
    if not text.strip() or "\0" in text or len(text) > 2000:
        raise ValueError("Укажите текст сообщения: /message ваш текст (до 2000 символов).")
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    command = (
        "if(-not [Environment]::UserInteractive){throw 'An interactive Windows desktop is required.'}; "
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"$text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')); "
        "[Windows.Forms.MessageBox]::Show($text,'Kintler',"
        "[Windows.Forms.MessageBoxButtons]::OK,[Windows.Forms.MessageBoxIcon]::Information) | Out-Null"
    )
    return {"shell": "powershell", "command": command, "cwd": ".", "timeout": 120}
