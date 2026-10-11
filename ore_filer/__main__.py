import multiprocessing

# PyInstaller + multiprocessing.get_context("spawn") の組み合わせで必要。
# 子プロセス起動時に同じ exe が再実行されるため、GUI 初期化より先に呼ぶ必要がある。
multiprocessing.freeze_support()

from ore_filer.app import main

if __name__ == "__main__":
    raise SystemExit(main())
