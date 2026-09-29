import os
import tempfile
from typing import Optional, Tuple

import FreeCAD as FC
import FreeCADGui as FCGui
from PySide2.QtCore import Qt
from PySide2.QtWidgets import QApplication

# Ensure GUI is initialized for headless rendering
if not QApplication.instance():
    app = QApplication([])

def generate_screenshot(face: FC.DocumentObject, output_path: str) -> bool:
    """
21 |         doc = FC.App.newDocument()
22 |         FCGui.setActiveDocument(doc)
        # Create temporary document
24 |         # Create a new object to hold the face shape
25 |         obj = doc.addObject("Part::Feature", "FaceObject")
26 |         obj.Shape = face.Shape
        doc = FC.App.newDocument()
        doc = FC.App.newDocument()
        FCGui.setActiveDocument(doc)
        fc_face.Shape = face.Shape
26 |         doc.recompute()

        # Set up view
        view = FCGui.ActiveDocument.ActiveView
        if not view:
            FCGui.ActiveDocument.ActiveView = FCGui.showView()
            view = FCGui.ActiveDocument.ActiveView
        
        # Configure view settings
        view.setCameraType("Perspective")
        view.fitAll()

        # Render to image
img_path = output_path
FCGui.ActiveDocument.ActiveView.saveImage(img_path, 1024, 768, "PNG")
        
        # Copy to output path
        if not os.path.exists(os.path.dirname(output_path)):
            os.makedirs(os.path.dirname(output_path))
        os.rename(img_path, output_path)
        return True
    except Exception as e:
        print(f"Error generating screenshot: {str(e)}")
        return False

def create_failure_diagnostic(
    face_id: str,
    error_msg: str,
    image_path: Optional[str] = None
) -> dict:
    """
    Create structured diagnostic report with optional screenshot.
    """
    return {
        "face_id": face_id,
        "error": error_msg,
        "image_path": image_path,
        "timestamp": str(FC.Units.getCurrentTime()),
    }

# Example usage
if __name__ == "__main__":
    # Test with a sample face (requires FreeCAD to be running)
    doc = FC.getDocument("Unnamed")
    if not doc:
        print("No document found. Run in FreeCAD GUI first.")
        exit(1)
    
    faces = [f for f in doc.Objects if hasattr(f, 'Shape') and f.Shape.Faces]
    if faces:
        test_face = faces[0].Shape.Faces[0]
        output_path = "./diagnostic_screenshot.png"
        success = generate_screenshot(test_face, output_path)
        print(f"Screenshot {'success' if success else 'failed'}: {output_path}")
