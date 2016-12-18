import argparse
import numpy as np
import io
import os
import six
import time
from PIL import Image
from six.moves import cPickle as pickle

import chainer
from chainer import cuda, Variable, optimizers, serializers
import chainer.functions as F
import chainer.links as L
import net


latent_size = 100
image_size = 64

def parse_args():
    parser = argparse.ArgumentParser(description='Stack-1 GAN trainer')
    parser.add_argument('--dataset', '-d', default='dataset/images.pkl', type=str, help='dataset file path')
    parser.add_argument('--gpu', '-g', default=-1, type=int, help='GPU ID (negative value indicates CPU)')
    parser.add_argument('--batch-size', '-b', default=64, type=int, help='batch size')
    parser.add_argument('--input', '-i', default=None, type=str, help='input model file path without extension')
    parser.add_argument('--output', '-o', required=True, type=str, help='output model file path without extension')
    parser.add_argument('--epoch', '-e', default=50, type=int, help='number of epochs')
    parser.add_argument('--save-epoch', default=1, type=int, help='number of epochs for saving intervals')
    parser.add_argument('--lr-decay', default=10, type=int, help='number of epochs for learning rate decay')
    parser.add_argument('--out-image-dir', default=None, type=str, help='output directory to output images')
    return parser.parse_args()

def update(gen, dis, optimizer_gen, optimizer_dis, x_batch):
    xp = gen.xp
    batch_size = len(x_batch)

    # from generated image
    z = xp.random.normal(0, 1, (batch_size, latent_size)).astype(np.float32)
    x_gen = gen(z)
    y_gen = dis(x_gen)
    loss_gen = F.sigmoid_cross_entropy(y_gen, xp.zeros((batch_size, 1), dtype=np.int32))
    loss_dis = F.sigmoid_cross_entropy(y_gen, xp.ones((batch_size, 1), dtype=np.int32))
    # from real image
    y = dis(xp.asarray(x_batch))
    loss_dis += F.sigmoid_cross_entropy(y, xp.zeros((batch_size, 1), dtype=np.int32))

    gen.cleargrads()
    loss_gen.backward()
    optimizer_gen.update()

    dis.cleargrads()
    loss_dis.backward()
    optimizer_dis.update()

    return float(loss_gen.data), float(loss_dis.data)

def train(gen, dis, optimizer_gen, optimizer_dis, images, epoch_num, output_path, lr_decay=10, save_epoch=1, batch_size=64, out_image_dir=None):
    xp = gen.xp
    out_image_row_num = 10
    out_image_col_num = 10
    z_out_image =  chainer.Variable(xp.random.uniform(-1, 1, (out_image_row_num * out_image_col_num, latent_size)).astype(np.float32), volatile=True)
    x_batch = np.zeros((batch_size, 3, image_size, image_size), dtype=np.float32)
    iterator = chainer.iterators.SerialIterator(images, batch_size)
    sum_loss_gen = 0
    sum_loss_dis = 0
    num_loss = 0
    last_clock = time.clock()
    for batch_images in iterator:
        w = 128
        h = 128
        for j, image in enumerate(batch_images):
            offset_x = np.random.randint(8) + 21
            offset_y = np.random.randint(8) + 51
            mirror = np.random.randint(2)
            with io.BytesIO(image) as b:
                pixels = np.asarray(Image.open(b).convert('RGB').crop((offset_x, offset_y, offset_x + w, offset_y + h)).resize((image_size, image_size)))
                pixels = pixels.astype(np.float32).transpose((2, 0, 1))
                if mirror == 1:
                    x_batch[j,...] = pixels[:,:,::-1] / 127.5 - 1
                else:
                    x_batch[j,...] = pixels / 127.5 - 1
        loss_gen, loss_dis = update(gen, dis, optimizer_gen, optimizer_dis, x_batch)
        sum_loss_gen += loss_gen
        sum_loss_dis += loss_dis
        num_loss += 1
        if iterator.is_new_epoch:
            epoch = iterator.epoch
            current_clock = time.clock()
            print('epoch {} done {}s elapsed'.format(epoch, current_clock - last_clock))
            print('gen loss: {}'.format(sum_loss_gen / num_loss))
            print('dis loss: {}'.format(sum_loss_dis / num_loss))
            last_clock = current_clock
            sum_loss_gen = 0
            sum_loss_dis = 0
            num_loss = 1
            if iterator.epoch % lr_decay == 0:
                optimizer_gen.alpha *= 0.5
                optimizer_dis.alpha *= 0.5
            if iterator.epoch % save_epoch == 0:
                if out_image_dir is not None:
                    image = gen(z_out_image, train=False).data
                    image = ((cuda.to_cpu(image) + 1) * 127.5)
                    image = image.clip(0, 255).astype(np.uint8)
                    image = image.reshape(out_image_row_num, out_image_col_num, 3, image_size, image_size)
                    image = image.transpose((0, 3, 1, 4, 2))
                    image = image.reshape((out_image_row_num * image_size, out_image_col_num * image_size, 3))
                    Image.fromarray(image).save(os.path.join(out_image_dir, '{0:04d}.png'.format(epoch)))
                serializers.save_npz('{0}_{1:03d}.gen.model'.format(output_path, epoch), gen)
                serializers.save_npz('{0}_{1:03d}.gen.state'.format(output_path, epoch), optimizer_gen)
                serializers.save_npz('{0}_{1:03d}.dis.model'.format(output_path, epoch), dis)
                serializers.save_npz('{0}_{1:03d}.dis.state'.format(output_path, epoch), optimizer_dis)

def main():
    args = parse_args()
    gen = net.Generator1()
    dis = net.Discriminator1()

    gpu_device = None
    if args.gpu >= 0:
        device_id = args.gpu
        cuda.get_device(device_id).use()
        gen.to_gpu(device_id)
        dis.to_gpu(device_id)

    optimizer_gen = optimizers.Adam(alpha=0.0002, beta1=0.5)
    optimizer_gen.setup(gen)
    optimizer_gen.add_hook(chainer.optimizer.WeightDecay(0.00001))
    optimizer_dis = optimizers.Adam(alpha=0.0002, beta1=0.5)
    optimizer_dis.setup(dis)
    optimizer_dis.add_hook(chainer.optimizer.WeightDecay(0.00001))

    if args.input != None:
        serializers.load_npz(args.input + '.gen.model', gen)
        serializers.load_npz(args.input + '.gen.state', optimizer_gen)
        serializers.load_npz(args.input + '.dis.model', dis)
        serializers.load_npz(args.input + '.dis.state', optimizer_dis)

    if args.out_image_dir != None:
        if not os.path.exists(args.out_image_dir):
            try:
                os.mkdir(args.out_image_dir)
            except:
                print 'cannot make directory {}'.format(args.out_image_dir)
                exit()
        elif not os.path.isdir(args.out_image_dir):
            print 'file path {} exists but is not directory'.format(args.out_image_dir)
            exit()

    with open(args.dataset, 'rb') as f:
        images = pickle.load(f)

    train(gen, dis, optimizer_gen, optimizer_dis, images, args.epoch, batch_size=args.batch_size, save_epoch=args.save_epoch, lr_decay=args.lr_decay, output_path=args.output, out_image_dir=args.out_image_dir)

if __name__ == '__main__':
    main()
