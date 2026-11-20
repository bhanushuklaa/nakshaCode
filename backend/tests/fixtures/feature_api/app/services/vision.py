import cv2
from ultralytics import YOLO
model = YOLO("best.pt")

def detect_defects(image_path):
    img = cv2.imread(image_path)
    results = model(img)
    return [r.boxes for r in results]
