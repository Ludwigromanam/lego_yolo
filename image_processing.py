from utils.im_transform import imcv2_recolor, imcv2_affine_trans
from utils.box import BoundBox, box_iou, prob_compare
import numpy as np
import cv2
import os


def _fix(obj, dims, scale, offs):
    for i in range(1, 5):
        dim = dims[(i + 1) % 2]
        off = offs[(i + 1) % 2]
        obj[i] = int(obj[i] * scale - off)
        obj[i] = max(min(obj[i], dim), 0)


def preprocess(im, inp_size, allobj=None):
    """
    Takes an image, return it as a numpy tensor that is readily
    to be fed into tfnet. If there is an accompanied annotation (allobj),
    meaning this preprocessing is serving the train process, then this
    image will be transformed with random noise to augment training data,
    using scale, translation, flipping and recolor. The accompanied
    parsed annotation (allobj) will also be modified accordingly.
    """
    if type(im) is not np.ndarray:
        im = cv2.imread(im)

    if allobj is not None:  # in training mode
        result = imcv2_affine_trans(im)
        im, dims, trans_param = result
        scale, offs, flip = trans_param
        for obj in allobj:
            _fix(obj, dims, scale, offs)
            if not flip:
                continue
            obj_1_ = obj[1]
            obj[1] = dims[0] - obj[3]
            obj[3] = dims[0] - obj_1_
        im = imcv2_recolor(im)

    h, w, c = inp_size
    imsz = cv2.resize(im, (h, w))
    imsz = imsz / 255.
    imsz = imsz[:, :, ::-1]
    if allobj is None:
        return imsz
    return imsz  # , np.array(im) # for unit testing


def postprocess(net_out, im, meta, save=True):
    """
    Takes net output, draw predictions, save to disk
    Args:
         - net_out (np vector) output of the last layer
         - im (file path to the input image or np array)
             (we draw the bb on the input image)
         - save the result to a file or display with openCV
    """

    def _to_color(indx, base):
        # return (b, r, g) tuple
        base2 = base * base
        b = 2 - indx / base2
        r = 2 - (indx % base2) / base
        g = 2 - (indx % base2) % base
        return (b * 127, r * 127, g * 127)
    colors = list()
    base = int(np.ceil(pow(meta['classes'], 1. / 3)))
    for x in range(len(meta['labels'])):
        colors += [_to_color(x, base)]
    meta['colors'] = colors

    threshold, sqrt = meta['threshold'], meta['sqrt'] + 1
    C, B, S = meta['classes'], meta['num'], meta['side']
    colors, labels = meta['colors'], meta['labels']

    # Here we divide the output vector
    # into class probabilities, confidence and cords
    boxes = []
    SS = S * S  # number of grid cells
    prob_size = SS * C  # class probabilities
    conf_size = SS * B  # confidences for each grid cell
    probs = net_out[0: prob_size]
    confs = net_out[prob_size: (prob_size + conf_size)]
    cords = net_out[(prob_size + conf_size):]
    probs = probs.reshape([SS, C])
    confs = confs.reshape([SS, B])
    cords = cords.reshape([SS, B, 4])

    # define the bounding boxes
    for grid in range(SS):
        for b in range(B):
            bx = BoundBox(C)
            bx.c = confs[grid, b]
            bx.x = (cords[grid, b, 0] + grid % S) / S
            bx.y = (cords[grid, b, 1] + grid // S) / S
            bx.w = cords[grid, b, 2] ** sqrt
            bx.h = cords[grid, b, 3] ** sqrt
            p = probs[grid, :] * bx.c
            p *= (p > threshold)
            bx.probs = p
            boxes.append(bx)

    # non max suppress boxes
    for c in range(C):
        for i in range(len(boxes)):
            boxes[i].class_num = c
        boxes = sorted(boxes, cmp=prob_compare)
        for i in range(len(boxes)):
            boxi = boxes[i]
            if boxi.probs[c] == 0:
                continue
            for j in range(i + 1, len(boxes)):
                boxj = boxes[j]
                if box_iou(boxi, boxj) >= .4:
                    boxes[j].probs[c] = 0.
    # Starting from here we add the actual boxes with
    # nice color on a picture to display or to save.
    if type(im) is not np.ndarray:
        imgcv = cv2.imread(im)
    else:
        imgcv = im
    h, w, _ = imgcv.shape
    for b in boxes:
        max_indx = np.argmax(b.probs)
        max_prob = b.probs[max_indx]
        if max_prob > threshold:
            label = meta['labels'][max_indx]
            left = int((b.x - b.w / 2.) * w)
            right = int((b.x + b.w / 2.) * w)
            top = int((b.y - b.h / 2.) * h)
            bot = int((b.y + b.h / 2.) * h)
            if left < 0:
                left = 0
            if right > w - 1:
                right = w - 1
            if top < 0:
                top = 0
            if bot > h - 1:
                bot = h - 1
            thick = int((h + w) / 300)
            cv2.rectangle(imgcv,
                          (left, top), (right, bot),
                          meta['colors'][max_indx], thick)
            mess = '{}:{:.3f}'.format(label, max_prob)
            cv2.putText(imgcv, mess, (left, top - 12),
                        0, 1e-3 * h, meta['colors'][max_indx], thick / 5)

    if not save:
        return imgcv
    outfolder = 'out'
    if not os.path.isdir(outfolder):
        os.mkdir(outfolder)
    img_name = os.path.join(outfolder, im.split('/')[-1])
    cv2.imwrite(img_name, imgcv)
