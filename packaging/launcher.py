import sys
from pathlib import Path

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from skullbase_corridor.desktop.selftest import run
        raise SystemExit(run(Path(sys.argv[2]).resolve()))
    elif (len(sys.argv) == 3 and sys.argv[1] == "--planning-demo") or sys.argv[1:] == ["--segmented-demo"]:
        from PySide6 import QtCore, QtWidgets
        from skullbase_corridor.desktop.app import MainWindow
        app = QtWidgets.QApplication([])
        app.setApplicationName("Skull-Base Corridor")
        segmented = sys.argv[1] == "--segmented-demo"
        window = MainWindow(None if segmented else sys.argv[2])
        window.show()
        QtCore.QTimer.singleShot(200, window.open_planning_phantom if segmented else window.start_analysis)
        raise SystemExit(app.exec())
    else:
        from skullbase_corridor.desktop.app import main
        raise SystemExit(main())
