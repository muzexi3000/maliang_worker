#!--coding=utf-8
# 登录
import argparse
import asyncio
import datetime
import logging
import os
import pathlib
import signal
import sys

import aircv as ac
import django
import nest_asyncio
import pyperclip
import zmq
from asgiref.sync import async_to_sync, sync_to_async
from easyprocess import EasyProcess
from pyppeteer import launch
from pyppeteer_stealth import stealth

fmt_str = '%(asctime)s - %(message)s'
formatter = logging.Formatter(fmt_str)
logging.basicConfig(level=logging.INFO, format=fmt_str)

project_root = str(pathlib.Path(__file__).parent.parent)
sys.path.append(project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'journey_bot.settings')
django.setup()

from app.models import Account, ImagineTask, TaskStatus
from django.conf import  settings


nest_asyncio.apply()

exit_signal = False


# context = zmq.Context()
# socket = context.socket(zmq.PULL)
# socket.connect("tcp://127.0.0.1:5559")


async def main(login_account, login_password, nick_name, tryTimes=3):
    temp_dir = os.path.join(project_root, 'temporary', login_account)
    pathlib.Path(temp_dir).mkdir(parents=True, exist_ok=True)
    screenshot_path = os.path.join(temp_dir, f"{login_account}.png")
    browser = await launch(headless=False, dumpio=True, autoClose=False, userDataDir=temp_dir,
                           args=['--no-sandbox', '-window-size=1024,768', '--disable-infobars', '--disable-gpu',
                                 '--proxy-server='+settings.ENV("proxy"), '--ignore-certificate-errors'])  # 进入有头模式
    try:
        room_url = "https://discord.com/channels/1076441592879136859/1076441593512464406"
        # t = filter(lambda x: x.url == room_url, browser.targets())
        # print(browser.targets())
        # print(t.page())
        page = (await browser.pages())[0]  # 打开新的标签页
        await page.bringToFront()
        await page.setViewport({'width': 1024, 'height': 700})  # 页面大小一致
        await stealth(page)
        await page.goto(room_url, waitUntil=[
            'load',  # 等待 “load” 事件触发
            'domcontentloaded',  # 等待 “domcontentloaded” 事件触发
        ])
        while not exit_signal and tryTimes > 0:
            try:
                check_state = await  check_page_state(page, nick_name)
                print("check_state ", check_state)
                if check_state == 2:
                    check_state = await login(login_account, login_password, nick_name, page)
                    tryTimes -= 1
                if check_state == 1:
                    gen_start_at = datetime.datetime.now() + datetime.timedelta(minutes=-3)
                    task = await sync_to_async(
                        lambda: ImagineTask.objects.filter(status=TaskStatus.NEW, prompt__isnull=False,
                                                           time__gt=gen_start_at).order_by(
                            "-priority", "time").first())()
                    if task:
                        print("ImagineTask: ", task)
                        cmd = task.prompt
                        print("cmd:", cmd)
                        task.status = TaskStatus.RUNNING
                        await  sync_to_async(lambda: task.save())()
                        print("接收到提示词:", cmd)
                        await send_cmd(page, screenshot_path, cmd)
                    tryTimes = 3
                    await asyncio.sleep(0.2)
                else:
                    logging.info("页面状态不正确,%s", check_state)
                    await page.screenshot({"path": screenshot_path})
                    tryTimes -= 1
            except Exception as ex:
                print(ex)
                logging.info("发送指令异常,%s", ex)
                tryTimes -= 1
                await page.screenshot({"path": screenshot_path})
    except Exception as ex:
        print(ex)
        logging.info("页面访问异常,%s", ex)
        await page.screenshot({"path": screenshot_path})
    finally:
        await browser.close()


async def send_cmd(page, screenshot_path, cmd):
    await page.waitForFunction(f"window.document.body.textContent.includes('发消息') ||window.document.body.textContent.includes('Message #常规')")
    await asyncio.sleep(0.2)
    await page.screenshot({"path": screenshot_path})
    pos = locateOnScreen(screenshot_path, os.path.join(project_root,"discord_bot/imagine.png"),os.path.join(project_root,"discord_bot/imagine1.png"))
    print("imagine position:", pos)
    await page.mouse.click(*pos)
    await page.keyboard.type("/imagine")
    ready_keywords = "Create images with Midjourney"
    await page.waitForFunction(f"window.document.body.textContent.includes('{ready_keywords}')")
    await page.keyboard.press("Enter")
    # sudo apt install xclip
    pyperclip.copy(cmd)
    await page.keyboard.down('Control')
    await page.keyboard.press('V')
    await page.keyboard.up('Control')
    # await page.keyboard.type(cmd)
    await  asyncio.sleep(0.5)
    await page.keyboard.press("Enter")
    logging.info("发送提示词:%s", cmd)
    return pos


async def login(login_account, login_password, nick_name, page):
    await page.waitForSelector('input[name="email"]')
    await page.type('input[name="email"]', login_account)
    await  asyncio.sleep(0.2)
    await page.type('input[name="password"]', login_password)
    await  asyncio.sleep(0.2)
    await page.waitForSelector('button[type="submit"]')
    await page.click('button[type="submit"]')
    await page.waitForXPath(f'//*[contains(text(),{nick_name})]')
    return 1


def locateOnScreen(src, dst1,dst2):
    source = ac.imread(src)
    target1 = ac.imread(dst1)
    target2 = ac.imread(dst2)
    result = ac.find_template(source, target1, 0.8)
    if not result:
        result = ac.find_template(source, target2, 0.8)
    print("find_template", result)
    return result and result["result"]


async def check_page_state(page, nick_name):
    login_page_text = "Forgot your password"
    login_again = "Choose an account"
    await page.waitForFunction(
        f"""window.document.body.textContent.includes('{nick_name}')|| window.document.body.textContent.includes('{login_page_text}')""")
    check_js = f"""node=>{{
        let content = window.document.body.textContent;
        if(content.includes("{login_again}"))  return 3;
        if(content.includes("{nick_name}"))  return 1;
        if(content.includes("{login_page_text}"))  return 2;
        return 4;
     }}
    """
    check_state = await page.Jeval("body", check_js)
    if check_state == 3:
        btns = await page.Jx('//button/div[contains(text(),"Log in")]')
        print("btns", btns)
        await asyncio.sleep(0.2)
        await btns[0].click()
        await asyncio.sleep(0.2)
        check_state = 2
    return check_state


def start(login_account, login_password, nick_name):
    g = asyncio.gather(main(login_account, login_password, nick_name))
    asyncio.run(g, debug=True)


def start_main_thread(login_account, login_password, nick_name):
    pass
    # os.environ["DISPLAY"] = ":2"
    # with EasyProcess([sys.executable, os.path.abspath(__file__), '--login_account', login_account, '--login_password',
    #                   login_password], env=os.environ) as p:
    #     out = p.stdout
    #     logging.info(out)
    #     m = re.findall("######(.*?)######", out)
    #     return m[0] if m and m[0].strip() != 'None' else None


def exit_handle():
    global exit_signal
    exit_signal = True
    sys.exit()


if __name__ == '__main__':
    # --login_account  luwu0755@qq.com --login_password  luWU2021 --nick_name 卢大叔
    parser = argparse.ArgumentParser(description='discord login')
    parser.add_argument('--login_account', dest='login_account',
                        type=str, help='资金账号', required=True )
    parser.add_argument('--login_password', dest='login_password',
                        type=str, help='登录密码', required=True )
    parser.add_argument('--nick_name', dest='nick_name',
                        type=str, help='登录密码', required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, exit)
    # with EasyProcess(["/usr/bin/Xvfb", ":2" ,"-ac", "-screen", "0" ,"1024x768x24"]) as proc:
    start(args.login_account, args.login_password, args.nick_name)
    print(f"######started######")
