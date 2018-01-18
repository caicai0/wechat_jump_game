# coding: utf-8
import os
import shutil
import time
import math
import wda
from PIL import Image, ImageDraw
import random
import json
import cv2


# === 思路 ===
# 核心：每次落稳之后截图，根据截图算出棋子的坐标和下一个块顶面的中点坐标，
#      根据两个点的距离乘以一个时间系数获得长按的时间
# 识别棋子：靠棋子的颜色来识别位置，通过截图发现最下面一行大概是一条直线，就从上往下一行一行遍历，
#         比较颜色（颜色用了一个区间来比较）找到最下面的那一行的所有点，然后求个中点，
#         求好之后再让 Y 轴坐标减小棋子底盘的一半高度从而得到中心点的坐标
# 识别棋盘：靠底色和方块的色差来做，从分数之下的位置开始，一行一行扫描，由于圆形的块最顶上是一条线，
#          方形的上面大概是一个点，所以就用类似识别棋子的做法多识别了几个点求中点，
#          这时候得到了块中点的 X 轴坐标，这时候假设现在棋子在当前块的中心，
#          根据一个通过截图获取的固定的角度来推出中点的 Y 坐标
# 最后：根据两点的坐标算距离乘以系数来获取长按时间（似乎可以直接用 X 轴距离）


# TODO: 解决定位偏移的问题
# TODO: 看看两个块中心到中轴距离是否相同，如果是的话靠这个来判断一下当前超前还是落后，便于矫正
# TODO: 一些固定值根据截图的具体大小计算
# TODO: 直接用 X 轴距离简化逻辑

with open('config.json', 'r') as f:
    config = json.load(f)


# Magic Number，不设置可能无法正常执行，请根据具体截图从上到下按需设置
under_game_score_y = config['under_game_score_y']     # 截图中刚好低于分数显示区域的 Y 坐标，300 是 1920x1080 的值，2K 屏、全面屏请根据实际情况修改
press_coefficient = config['press_coefficient']       # 长按的时间系数，请自己根据实际情况调节
piece_base_height_1_2 = config['piece_base_height_1_2']   # 二分之一的棋子底座高度，可能要调节
piece_body_width = config['piece_body_width']             # 棋子的宽度，比截图中量到的稍微大一点比较安全，可能要调节
time_coefficient = config['press_coefficient']
max_score = config['max_score']

def loadConfig():
    with open('config.json', 'r') as f:
        config = json.load(f)
        global max_score
        max_score = config['max_score']
        

def store(data):
    with open('config.json', 'w') as json_file:
        json_file.write(json.dumps(data))

# 模拟按压的起始点坐标，需要自动重复游戏请设置成“再来一局”的坐标
if config.get('swipe'):
    swipe = config['swipe']
else:
    swipe = {
        "x1": 320,
        "y1": 410,
        "x2": 320,
        "y2": 410
    }

c = wda.Client()
s = c.session()

screenshot_backup_dir = 'screenshot_backups/'

templates = []
for i in range(0,9):
    image = cv2.imread("./template/"+str(i)+".png")
    grayImage = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    templates.append(grayImage)

if not os.path.isdir(screenshot_backup_dir):
    os.mkdir(screenshot_backup_dir)


def pull_screenshot():
    c.screenshot('1.png')


def jump(distance):
    press_time = distance * time_coefficient / 1000
    # print('press time: {}'.format(press_time))
    s.tap_hold(200, 200, press_time)


def backup_screenshot(ts):
    # 为了方便失败的时候 debug
    if not os.path.isdir(screenshot_backup_dir):
        os.mkdir(screenshot_backup_dir)
    shutil.copy('1.png', '{}{}.png'.format(screenshot_backup_dir, ts))


def save_debug_creenshot(ts, im, piece_x, piece_y, board_x, board_y):
    draw = ImageDraw.Draw(im)
    # 对debug图片加上详细的注释
    draw.line((piece_x, piece_y) + (board_x, board_y), fill=2, width=3)
    draw.line((piece_x, 0, piece_x, im.size[1]), fill=(255, 0, 0))
    draw.line((0, piece_y, im.size[0], piece_y), fill=(255, 0, 0))
    draw.line((board_x, 0, board_x, im.size[1]), fill=(0, 0, 255))
    draw.line((0, board_y, im.size[0], board_y), fill=(0, 0, 255))
    draw.ellipse((piece_x - 10, piece_y - 10, piece_x + 10, piece_y + 10), fill=(255, 0, 0))
    draw.ellipse((board_x - 10, board_y - 10, board_x + 10, board_y + 10), fill=(0, 0, 255))
    del draw
    im.save('{}{}_d.png'.format(screenshot_backup_dir, ts))


def set_button_position(im):
    # 将swipe设置为 `再来一局` 按钮的位置
    global swipe_x1, swipe_y1, swipe_x2, swipe_y2
    w, h = im.size
    left = w / 2
    top = 1003 * (h / 1280.0) + 10
    swipe_x1, swipe_y1, swipe_x2, swipe_y2 = left, top, left, top


def find_piece_and_board(im):
    w, h = im.size

    # print("size: {}, {}".format(w, h))

    piece_x_sum = 0
    piece_x_c = 0
    piece_y_max = 0
    board_x = 0
    board_y = 0
    scan_x_border = int(w / 8)  # 扫描棋子时的左右边界
    scan_start_y = 0  # 扫描的起始y坐标
    im_pixel = im.load()

    # 以50px步长，尝试探测scan_start_y
    for i in range(under_game_score_y, h, 50):
        last_pixel = im_pixel[0, i]
        for j in range(1, w):
            pixel = im_pixel[j, i]

            # 不是纯色的线，则记录scan_start_y的值，准备跳出循环
            if pixel[0] != last_pixel[0] or pixel[1] != last_pixel[1] or pixel[2] != last_pixel[2]:
                scan_start_y = i - 50
                break

        if scan_start_y:
            break

    # print("scan_start_y: ", scan_start_y)

    # 从scan_start_y开始往下扫描，棋子应位于屏幕上半部分，这里暂定不超过2/3
    for i in range(scan_start_y, int(h * 2 / 3)):
        for j in range(scan_x_border, w - scan_x_border):  # 横坐标方面也减少了一部分扫描开销
            pixel = im_pixel[j, i]
            # 根据棋子的最低行的颜色判断，找最后一行那些点的平均值，这个颜色这样应该 OK，暂时不提出来
            if (50 < pixel[0] < 60) and (53 < pixel[1] < 63) and (95 < pixel[2] < 110):#颜色范围
                piece_x_sum += j
                piece_x_c += 1
                piece_y_max = max(i, piece_y_max)

    if not all((piece_x_sum, piece_x_c)):
        return 0, 0, 0, 0
    piece_x = piece_x_sum / piece_x_c
    piece_y = piece_y_max - piece_base_height_1_2  # 上移棋子底盘高度的一半

    board_x_sum = 0
    board_x_c = 0
    count = 0
    for i in range (int (h / 3), int (h * 2 / 3)):
        last_pixel = im_pixel[0, i]
        if count>10:
            board_top_y = i
            break

        if board_x_sum:
            count += 1

        for j in range(w):
            pixel = im_pixel[j, i]
            # 修掉脑袋比下一个小格子还高的情况的 bug
            if abs(j - piece_x) < piece_body_width:
                continue

            # 修掉圆顶的时候一条线导致的小 bug，这个颜色判断应该 OK，暂时不提出来
            if abs(pixel[0] - last_pixel[0]) + abs(pixel[1] - last_pixel[1]) + abs(pixel[2] - last_pixel[2]) > 10:
                board_x_sum += j
                board_x_c += 1

    if board_x_sum:
        board_x = board_x_sum / board_x_c

    # 按实际的角度来算，找到接近下一个 board 中心的坐标 这里的角度应该是30°,值应该是tan 30°, math.sqrt(3) / 3
    board_y = piece_y - abs(board_x - piece_x) * math.sqrt(3) / 3
    if board_y < board_top_y+45:
        board_y = board_top_y+45
        # print('board_top_y:{}',board_top_y)
    if not all((board_x, board_y)):
        return 0, 0, 0, 0

    return piece_x, piece_y, board_x, board_y


def match(src, template):
    image_matched = cv2.matchTemplate(src,template,cv2.TM_CCOEFF_NORMED)
    minVal, maxVal, minLoc, maxLoc = cv2.minMaxLoc(image_matched)
    return maxVal

def readNumber():
    im = cv2.imread("./1.png")
    h,w,c = im.shape
    numberImage = im[236:236+94,144:w]
    grayImage = cv2.cvtColor(numberImage,cv2.COLOR_BGR2GRAY)
    retVal,image = cv2.threshold(grayImage,85,255,cv2.THRESH_BINARY)

    number = 0
    for i in range(0,9):
        oneBit = image[0:94,(i*92):(i*92+74)]
        index = 0
        same = 0
        for j in range(0,9) :
            res = match(oneBit,templates[j])
            if res > same :
                same = res
                index = j
        if same < 0.5 :
            break
        else:
            number = number*10+index
    return number

def main():
    offset = 0
    while True:
        pull_screenshot()

        score = readNumber()
        print("score:",score,"max_score:",max_score)
        if score > max_score :
            print("score 超出以前最高分")
            md = (score-max_score)/2
            config['max_score'] = max_score+3
            store(config)
            offset = 50*random.uniform(-md, md)+50
        else:
            offset = 0
            if score == 0 :
                loadConfig()#刷新最高分

        im = Image.open("./1.png")


        # 获取棋子和 board 的位置
        piece_x, piece_y, board_x, board_y = find_piece_and_board(im)
        ts = int(time.time())
        # print(ts, piece_x, piece_y, board_x, board_y)
        if piece_x == 0:
            s.tap(20,20)
            time.sleep(random.uniform(5, 20))
            s.tap(212,611)
        else:
            set_button_position(im)
            distance = math.sqrt((board_x - piece_x) ** 2 + (board_y - piece_y) ** 2)
            distance = distance + offset
            if distance <= 0 :
                distance = 200
            jump(distance)
            save_debug_creenshot(ts, im, piece_x, piece_y, board_x, board_y)
            backup_screenshot(ts)

        time.sleep(random.uniform(1, 3.1))   # 为了保证截图的时候应落稳了，多延迟一会儿


if __name__ == '__main__':
    main()
