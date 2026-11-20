"""Download the pinned offline English→Chinese reasoning translation model.

Run once on the deployment host: python scripts/setup_reasoning_translation.py
The API translates locally and never downloads model files at request time.
"""
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile
import io


URL = "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel"
SHA256 = "433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab"
ROOT = Path(__file__).resolve().parents[1] / "models" / "translation" / "en_zh"
FILES = ("sentencepiece.model", "model/model.bin", "model/config.json",
         "model/shared_vocabulary.json")


def main():
    if all((ROOT / name).is_file() for name in FILES):
        print(f"离线翻译模型已就绪：{ROOT}")
        return
    with urlopen(URL, timeout=180) as response:
        archive = response.read()
    if sha256(archive).hexdigest() != SHA256:
        raise SystemExit("英译中模型校验失败，未写入磁盘")
    with ZipFile(io.BytesIO(archive)) as source:
        for name in FILES:
            target = ROOT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read("translate-en_zh-1_9/" + name))
    print(f"离线翻译模型已安装：{ROOT}")


if __name__ == "__main__":
    main()
