#   d
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
""" Evaluate on CPU Platform. """
import os
import time
import logging
import argparse
import cv2
import numpy
from src.dataset import pt_dataset, pt_transform
import src.utils.functions_args as fa
from src.utils.p_util import AverageMeter as AM
from src.utils.p_util import intersectionAndUnion, check_makedirs, colorize
import mindspore.numpy as np
import mindspore
from mindspore import Tensor
import mindspore.dataset as ds
from mindspore import context
import mindspore.nn as nn
import mindspore.ops as ops
from mindspore.train.serialization import load_param_into_net, load_checkpoint


def get_log():
    """ Eval Logger """
    logger_name = "eval-logger"
    log = logging.getLogger(logger_name)
    log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    fmt = "[%(asctime)s %(levelname)s %(filename)s line %(lineno)d %(process)d] %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    log.addHandler(handler)
    return log


def main():
    """ The main function of the evaluate process """
    logger.info("=> creating PSPNet model ...")
    logger.info("Class number: %s", args.classes)

    value_scale = 255
    mean = [0.485, 0.456, 0.406]
    mean = [item * value_scale for item in mean]
    std = [0.229, 0.224, 0.225]
    std = [item * value_scale for item in std]
    gray_folder = os.path.join(args.result_path, 'gray')
    color_folder = os.path.join(args.result_path, 'color')
    test_transform = pt_transform.Compose([pt_transform.Normalize(mean=mean, std=std, is_train=False)])
    test_data = pt_dataset.SemData(
        split='val', data_root=args.data_root,
        data_list=args.val_list,
        transform=test_transform)
    test_loader = ds.GeneratorDataset(test_data, column_names=["data", "label"], shuffle=False)
    test_loader.batch(1)
    colors = numpy.loadtxt(args.color_txt).astype('uint8')
    names = [line.rstrip('\n') for line in open(args.name_txt)]

    from src.model import pspnet
    PSPNet = pspnet.PSPNet(
        feature_size=args.feature_size,
        num_classes=args.classes,
        backbone=args.backbone,
        pretrained=False,
        pretrained_path="",
        aux_branch=False,
        deep_base=True
    )
    ms_checkpoint = load_checkpoint(args.ckpt)
    load_param_into_net(PSPNet, ms_checkpoint, strict_load=True)
    PSPNet.set_train(False)
    test_model(test_loader, test_data.data_list, PSPNet, args.classes, mean, std, args.base_size, args.test_h,
               args.test_w, args.scales, gray_folder, color_folder, colors)
    if args.split != 'test':
        calculate_acc(test_data.data_list, gray_folder, args.classes, names, colors)


def net_process(model, image, mean, std=None, flip=True):
    """ Give the input to the model"""
    transpose = ops.Transpose()
    input_ = transpose(image, (2, 0, 1))  # (473, 473, 3) -> (3, 473, 473)
    mean = np.array(mean)
    std = np.array(std)
    if std is None:
        input_ = input_ - mean[:, None, None]
    else:
        input_ = (input_ - mean[:, None, None]) / std[:, None, None]

    expand_dim = ops.ExpandDims()
    input_ = expand_dim(input_, 0)
    if flip:
        flip_input = np.flip(input_, axis=3)
        concat = ops.Concat(axis=0)
        input_ = concat((input_, flip_input))
    model.set_train(False)
    output = model(input_)
    _, _, h_i, w_i = input_.shape
    _, _, h_o, w_o = output.shape
    if (h_o != h_i) or (w_o != w_i):
        bi_linear = nn.ResizeBilinear()
        output = bi_linear(output, size=(h_i, w_i), align_corners=True)
    softmax = nn.Softmax(axis=1)
    output = softmax(output)
    if flip:
        output = (output[0] + np.flip(output[1], axis=2)) / 2
    else:
        output = output[0]
    output = transpose(output, (1, 2, 0))  # Tensor
    output = output.asnumpy()
    return output


def calculate_acc(data_list, pred_folder, classes, names, colors):
    """ Calculation evaluating indicator """
    colors = colors.tolist()
    overlap_meter = AM()
    union_meter = AM()
    target_meter = AM()
    length = len(data_list)
    for index, (image_path, target_path) in enumerate(data_list):
        image_name = image_path.split('/')[-1].split('.')[0]
        pred = cv2.imread(os.path.join(pred_folder, image_name + '.png'), cv2.IMREAD_GRAYSCALE)
        if args.prefix == 'voc':
            target = cv2.imread(target_path)
            target = cv2.cvtColor(target, cv2.COLOR_BGR2RGB)
            anno_label = convert(target, colors)

        if args.prefix == 'ADE':
            anno_label = cv2.imread(target_path, cv2.IMREAD_GRAYSCALE)
            anno_label -= 1
        overlap, union, target = intersectionAndUnion(pred, anno_label, classes)
        overlap_meter.update(overlap)
        union_meter.update(union)
        target_meter.update(target)
        acc = sum(overlap_meter.val) / (sum(target_meter.val) + 1e-10)
        logger.info(
            'Evaluating {0}/{1} on image {2}, accuracy {3:.4f}.'.format(index + 1, length, image_name + '.png', acc))
    iou_class = overlap_meter.sum / (union_meter.sum + 1e-10)
    accuracy_class = overlap_meter.sum / (target_meter.sum + 1e-10)
    mIoU = numpy.mean(iou_class)
    mAcc = numpy.mean(accuracy_class)
    allAcc = sum(overlap_meter.sum) / (sum(target_meter.sum) + 1e-10)

    logger.info('Eval result: mIoU/mAcc/allAcc {:.4f}/{:.4f}/{:.4f}.'.format(mIoU, mAcc, allAcc))
    for i in range(classes):
        logger.info('Class_{} result: iou/accuracy {:.4f}/{:.4f}, name: {}.'.format(i, iou_class[i], accuracy_class[i],
                                                                                    names[i]))


def scale_proc(model, image, classes, crop_h, crop_w, h, w, mean, std=None, stride_rate=2 / 3):
    """ Process input size """
    ori_h, ori_w, _ = image.shape
    pad_h = max(crop_h - ori_h, 0)
    pad_w = max(crop_w - ori_w, 0)
    pad_h_half = int(pad_h / 2)
    pad_w_half = int(pad_w / 2)
    if pad_h > 0 or pad_w > 0:
        image = cv2.copyMakeBorder(image, pad_h_half, pad_h - pad_h_half, pad_w_half, pad_w - pad_w_half,
                                   cv2.BORDER_CONSTANT, value=mean)

    new_h, new_w, _ = image.shape
    image = Tensor.from_numpy(image)
    stride_h = int(numpy.ceil(crop_h * stride_rate))
    stride_w = int(numpy.ceil(crop_w * stride_rate))
    g_h = int(numpy.ceil(float(new_h - crop_h) / stride_h) + 1)
    g_w = int(numpy.ceil(float(new_w - crop_w) / stride_w) + 1)
    pred_crop = numpy.zeros((new_h, new_w, classes), dtype=float)
    count_crop = numpy.zeros((new_h, new_w), dtype=float)
    for idh in range(0, g_h):
        for idw in range(0, g_w):
            s_h = idh * stride_h
            e_h = min(s_h + crop_h, new_h)
            s_h = e_h - crop_h
            s_w = idw * stride_w
            e_w = min(s_w + crop_w, new_w)
            s_w = e_w - crop_w
            image_crop = image[s_h:e_h, s_w:e_w].copy()
            count_crop[s_h:e_h, s_w:e_w] += 1
            pred_crop[s_h:e_h, s_w:e_w, :] += net_process(model, image_crop, mean, std)
    pred_crop /= numpy.expand_dims(count_crop, 2)
    pred_crop = pred_crop[pad_h_half:pad_h_half + ori_h, pad_w_half:pad_w_half + ori_w]
    pred = cv2.resize(pred_crop, (w, h), interpolation=cv2.INTER_LINEAR)
    return pred


def get_parser():
    """
    Read parameter file
        -> for ADE20k: ./config/ade20k_pspnet50.yaml
        -> for voc2012: ./config/voc2012_pspnet50.yaml
    """
    parser = argparse.ArgumentParser(description='MindSpore Semantic Segmentation')
    parser.add_argument('--config', type=str, required=True, default='./config/ade20k_pspnet50.yaml',
                        help='config file')
    parser.add_argument('opts', help='see ./config/voc2012_pspnet50.yaml for all options', default=None,
                        nargs=argparse.REMAINDER)
    args_ = parser.parse_args()
    assert args_.config is not None
    cfg = fa.load_cfg_from_cfg_file(args_.config)
    if args_.opts is not None:
        cfg = fa.merge_cfg_from_list(cfg, args_.opts)
    return cfg


def test_model(data_iter, path_iter, model, classes, mean, std, origin_size, c_h, c_w, scales, gray_folder,
               color_folder, colors):
    """ Generate evaluate image """
    logger.info('>>>>>>>>>>>>>>>>Evaluation Start>>>>>>>>>>>>>>>>')
    model.set_train(False)
    batch_time = AM()
    data_time = AM()
    begin_time = time.time()
    scales_num = len(scales)
    img_num = len(path_iter)
    for i, (input_, _) in enumerate(data_iter):
        data_time.update(time.time() - begin_time)
        input_ = input_.asnumpy()
        image = numpy.transpose(input_, (1, 2, 0))
        h, w, _ = image.shape
        pred = numpy.zeros((h, w, classes), dtype=float)
        for ratio in scales:
            long_size = round(ratio * origin_size)
            new_h = long_size
            new_w = new_h
            if h < w:
                new_h = round(long_size / float(w) * h)
            else:
                new_w = round(long_size / float(h) * w)

            new_image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            pred = pred + scale_proc(model, new_image, classes, c_h, c_w, h, w, mean, std)
        pred = pred / scales_num
        pred = numpy.argmax(pred, axis=2)
        batch_time.update(time.time() - begin_time)
        begin_time = time.time()
        if ((i + 1) % 10 == 0) or (i + 1 == img_num):
            logger.info('Test: [infer {} images, {} images in total] '
                        'Data {data_time.val:.3f} ({data_time.avg:.3f}) '
                        'Batch {batch_time.val:.3f} ({batch_time.avg:.3f}).'.format(i + 1, img_num,
                                                                                    data_time=data_time,
                                                                                    batch_time=batch_time))
        check_makedirs(color_folder)
        check_makedirs(gray_folder)
        gray = numpy.uint8(pred)
        image_path, _ = path_iter[i]
        color = colorize(gray, colors)
        image_name = image_path.split('/')[-1].split('.')[0]
        cv2.imwrite(os.path.join(gray_folder, image_name + '.png'), gray)
        color.save(os.path.join(color_folder, image_name + '.png'))
    logger.info('<<<<<<<<<<<<<<<<< End Evaluation <<<<<<<<<<<<<<<<<')


def convert(label, colors):
    """Convert classification ids in labels."""
    annotation = numpy.zeros((label.shape[0], label.shape[1]))
    for i in range(len(label)):
        for j in range(len(label[i])):
            if colors.count(label[i][j].tolist()):
                annotation[i][j] = colors.index(label[i][j].tolist())
            else:
                annotation[i][j] = 0
    a = Tensor(annotation, dtype=mindspore.uint8)
    annotation = a.asnumpy()
    return annotation


if __name__ == '__main__':
    cv2.ocl.setUseOpenCL(False)
    context.set_context(mode=context.GRAPH_MODE, device_target="CPU", save_graphs=False)
    args = get_parser()
    logger = get_log()
    main()
