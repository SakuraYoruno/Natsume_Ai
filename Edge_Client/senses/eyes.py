# senses/eyes.py
import cv2
import os
from ultralytics import YOLO
from colorama import Fore, Style


class NatsumeEyes:
    def __init__(self, model_path="models/yolov8n.pt"):
        self.model_path = model_path
        self.model = self._load_model()

    def _load_model(self):
        """安全加载模型，处理空文件问题"""
        print(f"{Fore.YELLOW}👀 正在检查视觉模型...{Style.RESET_ALL}")

        # 1. 检查文件是否损坏 (存在但小于 1KB)
        if os.path.exists(self.model_path):
            if os.path.getsize(self.model_path) < 1024:
                print(f"{Fore.RED}⚠️ 检测到模型文件为空或损坏，正在删除以触发重新下载...{Style.RESET_ALL}")
                os.remove(self.model_path)

        # 2. 加载 (ultralytics 会自动下载缺失的模型)
        try:
            # verbose=False 关闭啰嗦的加载日志
            model = YOLO(self.model_path)
            print(f"{Fore.GREEN}👀 视觉系统 (YOLOv8) 就绪!{Style.RESET_ALL}")
            return model
        except Exception as e:
            print(f"{Fore.RED}❌ 视觉模型加载失败: {e}{Style.RESET_ALL}")
            return None

    def see(self):
        """捕获并识别"""
        if not self.model:
            return []

        # 0 代表默认摄像头
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return []

        ret, frame = cap.read()
        cap.release()

        detected_objects = []
        if ret:
            # 运行推理
            results = self.model(frame, verbose=False)

            # 解析结果
            for result in results:
                for box in result.boxes:
                    conf = float(box.conf[0])
                    if conf > 0.5:  # 只要置信度 > 50% 的
                        class_id = int(box.cls[0])
                        name = self.model.names[class_id]
                        detected_objects.append(name)

        # 去重返回
        return list(set(detected_objects))