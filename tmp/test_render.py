import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# MUST be first: this adds FreeCAD's bundled site-packages to sys.path
from surface_assistant import freecad_setup  # noqa: F401

import sys
import time

import FreeCAD  # type: ignore
import FreeCADGui  # type: ignore
from PySide6 import QtWidgets  # type: ignore


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
FreeCADGui.showMainWindow()

doc = FreeCAD.newDocument("Test")
box = doc.addObject("Part::Box", "Box")
box.Length = 10
box.Width = 10
box.Height = 10
doc.recompute()

FreeCADGui.SendMsgToActiveView("ViewFit")
view = FreeCADGui.ActiveDocument.ActiveView
view.saveImage("/tmp/simple_box.png", 800, 600, "White")
print("Rendered /tmp/simple_box.png")

FreeCAD.closeDocument(doc.Name)