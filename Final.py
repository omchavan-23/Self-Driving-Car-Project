import tensorflow as tf
import cv2
import os
import numpy as np
import colorsys
from ultralytics import YOLO
from typing import List, Tuple
import concurrent.futures
import time

def custom_loss_fn(y_true, y_pred):
    """Custom loss function combining MSE and L2 regularization."""
    mse_loss = tf.reduce_mean(tf.square(y_true - y_pred))
    l2_loss = tf.add_n([tf.nn.l2_loss(v) for v in y_pred.trainable_variables])
    return mse_loss + 0.001 * l2_loss

# Define the Lambda function for custom operations
def custom_lambda(x):
    return tf.multiply(tf.math.atan(x), 2)

class SteeringAnglePredictor:
    def __init__(self, model_path: str):
        """Initialize and load the model."""
        self.model = tf.keras.models.load_model(model_path, custom_objects={'custom_loss_fn': custom_loss_fn, 'Lambda': custom_lambda})

    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """Preprocess the input image to match the model's expected input."""
        image = cv2.resize(image, (200, 66))  # Resize to (200, 66)
        image = image / 255.0  # Normalize pixel values
        return np.expand_dims(image, axis=0)  # Add batch dimension

    def predict_angle(self, image: np.ndarray) -> float:
        """Predict the steering angle from an input image."""
        image = self.preprocess_image(image)  # Use the instance's preprocess method
        radian_angle = self.model.predict(image)[0][0]
        return radian_angle * 180.0 / np.pi  # Convert radians to degrees

# Lane and Object segmentation with YOLO models
class ImageSegmentation:
    def __init__(self, lane_model_path: str, object_model_path: str):
        self.lane_model = YOLO(lane_model_path)
        self.object_model = YOLO(object_model_path)
        self.colors = self._generate_colors(len(self.object_model.names))

    @staticmethod
    def _generate_colors(num_classes: int) -> List[Tuple[int, int, int]]:
        """Generate distinct colors for each class."""
        colors = []
        for i in range(num_classes):
            hue = i / num_classes
            rgb = colorsys.hsv_to_rgb(hue, 0.9, 0.9)
            colors.append(tuple(int(x * 255) for x in rgb))
        return colors

    def process(self, img: np.ndarray, alpha: float = 0.5) -> np.ndarray:
        """Perform lane and object segmentation and overlay results."""
        overlay = img.copy()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_lane = executor.submit(self.lane_model.predict, img, conf=0.5)
            future_object = executor.submit(self.object_model.predict, img, conf=0.5)
            lane_results = future_lane.result()
            object_results = future_object.result()

        self._draw_lane_overlay(overlay, lane_results)
        self._draw_object_overlay(overlay, object_results)
        return cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)

    def _draw_lane_overlay(self, overlay: np.ndarray, lane_results):
        """Draw lane segmentation results."""
        for result in lane_results:
            if result.masks is None:
                continue
            for mask in result.masks.xy:
                points = np.int32([mask])
                cv2.fillPoly(overlay, points, (144, 238, 144))  # Light green for lanes

    def _draw_object_overlay(self, overlay: np.ndarray, object_results):
        """Draw object detection results."""
        for result in object_results:
            if result.masks is None:
                continue
            for mask, box in zip(result.masks.xy, result.boxes):
                class_id = int(box.cls[0])
                color = self.colors[class_id]
                points = np.int32([mask])
                cv2.fillPoly(overlay, points, color)
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
                label = f"{self.object_model.names[class_id]}: {box.conf[0]:.2f}"
                (label_w, label_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                cv2.rectangle(overlay, (x1, y1 - 20), (x1 + label_w, y1), color, -1)
                cv2.putText(overlay, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

class SelfDrivingCarSimulator:
    def __init__(self, steering_model: SteeringAnglePredictor, segmentation_model: ImageSegmentation, data_path: "data/driving_dataset", img_path: "data/steering_wheel_image.jpg"):
        self.steering_model = steering_model
        self.segmentation_model = segmentation_model
        self.data_path = "data/driving_dataset"
        self.img = cv2.imread("data/steering_wheel_image.jpg", 0)
        self.smoothed_angle = 0
        self.cols, self.rows = self.img.shape

    def start_simulation(self, frame_interval: float = 1 / 30):
        i = 0
        while True:
            start_time = time.time()
            full_image = cv2.imread(f"{self.data_path}/{i}.jpg")
            if full_image is None:
                print(f"Image {self.data_path}/{i}.jpg not found, ending simulation.")
                break

            resized_image = cv2.resize(full_image[-150:], (200, 66)) / 255.0
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future_steering = executor.submit(self.steering_model.predict_angle, resized_image)
                future_segmentation = executor.submit(self.segmentation_model.process, full_image)
                degrees = future_steering.result()
                segmented_image = future_segmentation.result()

            self._update_display(degrees, segmented_image, full_image)
            i += 1
            if time.time() - start_time < frame_interval:
                time.sleep(frame_interval - (time.time() - start_time))

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        cv2.destroyAllWindows()

    def _update_display(self, degrees, segmented_image, full_image):
        print(f"Predicted steering angle: {degrees:.2f} degrees")
        self.smoothed_angle += 0.2 * pow(abs((degrees - self.smoothed_angle)), 2.0 / 3.0) * (degrees - self.smoothed_angle) / abs(degrees - self.smoothed_angle)
        M = cv2.getRotationMatrix2D((self.cols / 2, self.rows / 2), -self.smoothed_angle, 1)
        dst = cv2.warpAffine(self.img, M, (self.cols, self.rows))

        cv2.imshow("Original Frame", full_image)
        cv2.imshow("Segmented Frame", segmented_image)
        cv2.imshow("Steering Wheel", dst)


if __name__ == "__main__":
    # Load models for steering prediction and segmentation
    steering_predictor = SteeringAnglePredictor("saved_model/regression_model/model_22.keras")
    image_segmentation = ImageSegmentation("saved_model/lane_segmentation_model/best.pt", "saved_model/object_detection_model/yolo11s-seg.pt")
    
    # Initialize the self-driving simulator
    simulator = SelfDrivingCarSimulator(steering_predictor, image_segmentation, "data", "steering_image.jpeg")
    simulator.start_simulation()
