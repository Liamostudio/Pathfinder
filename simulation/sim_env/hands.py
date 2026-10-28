"""Ground-truth hand detector for safety-loop plumbing, not vision validation."""
import cv2
import numpy as np
from lookandpick.adapters.base import PixelHand


class GroundTruthHandDetector:
    """Project 21 synthetic landmarks per hand using a captured camera's pose.

    Supply current frame metadata and hand list via callables. Each finger is a
    simple four-point chain; these are labelled ground truth, never model output.
    """
    def __init__(self, hands, metadata):
        self.hands, self.metadata = hands, metadata
        self.closed = False

    def detect(self, frame, timestamp_ms):
        if self.closed:
            raise RuntimeError('Detector closed')
        meta = self.metadata()
        transform = np.linalg.inv(np.array(meta['world_from_camera']))
        rvec = cv2.Rodrigues(transform[:3,:3])[0]
        result=[]
        for hand in self.hands():
            centre=np.array(hand.position)
            offsets=[[0,-.04,0]]
            for finger in range(5):
                for joint in range(4):
                    offsets.append([(finger-2)*.014,.01+joint*.017,0])
            points=np.array(offsets)+centre
            camera_points=points @ transform[:3,:3].T + transform[:3,3]
            if np.any(camera_points[:,2] <= 0):
                continue
            pixels=cv2.projectPoints(points,rvec,transform[:3,3],np.array(meta['K']),np.array(meta.get('dist',[0]*5)))[0].reshape(21,2)
            result.append(PixelHand(hand.id,tuple(map(tuple,pixels))))
        return result

    def close(self):
        self.closed=True
