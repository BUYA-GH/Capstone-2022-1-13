from collections import defaultdict
import numpy as np
import torch
import cv2

import torch.nn as nn
import torchvision.models as models
from torchvision.models import ResNet18_Weights
import torchvision.transforms as transforms
from torch.autograd import Variable

class SecondClassifier:
    def __init__(self, logo):
        # logo image RGB, float32 3*w*h
        logo = cv2.resize(logo, (224, 224))
        # logo = logo[:, :, ::-1].transpose(2, 0, 1)
        logo = logo[:, :, ::-1]
        self.logim = logo
        self.logo = np.ascontiguousarray(logo, dtype=np.float32)

        # need weights
        self.model = models.resnet18(weights=ResNet18_Weights.IMAGENET1K_V1)
        self.layer = self.model._modules.get('avgpool')
        self.model.eval()
        
        self.scaler = transforms.Resize(size=(224,224))
        self.normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        self.to_tensor = transforms.ToTensor()

        self.logo_vec = self.get_vector(self.logo)
        self.cos = nn.CosineSimilarity(dim=1, eps=1e-6)

        self.stat = defaultdict(int)

    def xyxy2xywh(self, x):
        # Convert nx4 boxes from [x1, y1, x2, y2] to [x, y, w, h] where xy1=top-left, xy2=bottom-right
        y = x.clone() if isinstance(x, torch.Tensor) else np.copy(x)
        y[:, 0] = (x[:, 0] + x[:, 2]) / 2  # x center
        y[:, 1] = (x[:, 1] + x[:, 3]) / 2  # y center
        y[:, 2] = x[:, 2] - x[:, 0]  # width
        y[:, 3] = x[:, 3] - x[:, 1]  # height
        return y

    def xywh2xyxy(self, x):
        # Convert nx4 boxes from [x, y, w, h] to [x1, y1, x2, y2] where xy1=top-left, xy2=bottom-right
        y = x.clone() if isinstance(x, torch.Tensor) else np.copy(x)
        y[:, 0] = x[:, 0] - x[:, 2] / 2  # top left x
        y[:, 1] = x[:, 1] - x[:, 3] / 2  # top left y
        y[:, 2] = x[:, 0] + x[:, 2] / 2  # bottom right x
        y[:, 3] = x[:, 1] + x[:, 3] / 2  # bottom right y
        return y

    def clip_coords(self, boxes, shape):
        # Clip bounding xyxy bounding boxes to image shape (height, width)
        if isinstance(boxes, torch.Tensor):  # faster individually
            boxes[:, 0].clamp_(0, shape[1])  # x1
            boxes[:, 1].clamp_(0, shape[0])  # y1
            boxes[:, 2].clamp_(0, shape[1])  # x2
            boxes[:, 3].clamp_(0, shape[0])  # y2
        else:  # np.array (faster grouped)
            boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, shape[1])  # x1, x2
            boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, shape[0])  # y1, y2

    # self.scale_coords(img.shape[2:], new_d[:, :4], im0[i].shape)
    def scale_coords(self, img1_shape, coords, img0_shape, ratio_pad=None):
        # Rescale coords (xyxy) from img1_shape to img0_shape
        if ratio_pad is None:  # calculate from img0_shape
            gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
            pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
        else:
            gain = ratio_pad[0][0]
            pad = ratio_pad[1]

        coords[:, [0, 2]] -= pad[0]  # x padding
        coords[:, [1, 3]] -= pad[1]  # y padding
        coords[:, :4] /= gain
        self.clip_coords(coords, img0_shape)
        return coords

    def get_vector(self, img):
        # 2. Create a PyTorch Variable with the transformed image
        t_img = Variable(self.normalize(self.to_tensor(img)).unsqueeze(0))

        # 3. Create a vector of zeros that will hold our feature vector
        #    The 'avgpool' layer has an output size of 512
        my_embedding = torch.zeros(1, 512, 1, 1)

        # 4. Define a function that will copy the output of a layer
        def copy_data(m, i, o):
            my_embedding.copy_(o.data)

        # 5. Attach that function to our selected layer
        h = self.layer.register_forward_hook(copy_data)

        # 6. Run the model on our transformed image
        self.model(t_img)

        # 7. Detach our copy function from the layer
        h.remove()

        # 8. Return the feature vector
        return my_embedding    

    def calculate_similarity(self, x, img, im0, thres=0.95):
        im0 = [im0] if isinstance(im0, np.ndarray) else im0
        new_x = []

        for i, d in enumerate(x):  # per image
            if d is not None and len(d):
                # print(d)
                new_d = d.clone().detach()

                before = len(new_d)
                after = before

                # Reshape and pad cutouts
                b = self.xyxy2xywh(new_d[:, :4])  # boxes
                # b[:, 2:] = b[:, 2:].max(1)[0].unsqueeze(1)  # rectangle to square
                # b[:, 2:] = b[:, 2:] * 1.3 + 30  # pad
                new_d[:, :4] = self.xywh2xyxy(b).long()

                # Rescale boxes from img_size to im0 size
                self.scale_coords(img.shape[2:], new_d[:, :4], im0[i].shape)

                # change box image
                # BGR to RGB
                # uint8 to float32
                sims = []
                for a_i, a in enumerate(new_d): # per detect
                    cutout = im0[i][int(a[1]):int(a[3]), int(a[0]):int(a[2])]

                    im = cv2.resize(cutout, (224, 224))
                    im = im[:, :, ::-1]
                    
                    # For Debugging
                    # cv2.imshow('logo', self.logim)
                    # cv2.waitKey(0)
                    # cv2.destroyAllWindows()

                    # cv2.imshow('logo', im)
                    # cv2.waitKey(0)
                    # cv2.destroyAllWindows()

                    im = np.ascontiguousarray(im, dtype=np.float32)  # uint8 to float32
                    im_vec = self.get_vector(im)

                    cos_sim = self.cos(self.logo_vec, im_vec) 
                    # sims.append(cos_sim[0,0,0])
                    
                    #print(cos_sim[0,0,0])
                    str_sim = str(cos_sim[0,0,0])
                    self.stat[str_sim[7:11]] += 1
                    # print(self.stat[str_sim[7:11]])

                    if thres > cos_sim[0, 0, 0]:
                        sims.append(a_i)
                        after -= 1

                d = np.delete(d, sims, 0)

            new_x.append(d)

        return new_x, self.stat
