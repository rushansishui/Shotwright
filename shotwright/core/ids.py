import secrets


def new_id(length: int = 12) -> str:
    """短的随机 id，只含小写字母和数字，可安全用作文件名。"""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
