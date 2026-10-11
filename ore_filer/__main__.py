import os
import sys

# SFTPワーカーモード: RemoteSessionProcess が ORE_FILER_SFTP_WORKER=1 で起動する子プロセス。
# Qt を一切ロードせず JSON IPC ループだけ実行して終了する。
if os.environ.get("ORE_FILER_SFTP_WORKER") == "1":
    from ore_filer.services.remote_process import _worker_main
    _worker_main()
    sys.exit(0)

from ore_filer.app import main

if __name__ == "__main__":
    raise SystemExit(main())
