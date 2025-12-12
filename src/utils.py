import time
import random
import pyautogui
from selenium.webdriver.common.action_chains import ActionChains

def human_sleep(average=3.0, variance=0.5):
    """
    Sleeps for a duration based on a Gaussian distribution.
    average: The target sleep time (mean).
    variance: How much the time can fluctuate (standard deviation).
    """
    sleep_time = abs(random.gauss(average, variance))
    # Ensure we never sleep less than 1 second to be safe
    sleep_time = max(1.0, sleep_time)
    time.sleep(sleep_time)

def smart_click(browser, element):
    """
    Moves the virtual cursor to the element, pauses (hovers), and then clicks
    to simulate human-styled focus and avoid instant-click detection.
    """
    try:
        actions = ActionChains(browser)
        actions.move_to_element(element).perform()
        human_sleep(0.5, 0.2)
        actions.click().perform()
    except Exception:
        element.click()

def scroll_slow(browser, scrollable_element, start=0, end=3600, step=100, reverse=False):
    if reverse:
        start, end = end, start
        step = -step

    for i in range(start, end, step):
        browser.execute_script("arguments[0].scrollTo(0, {})".format(i), scrollable_element)
        human_sleep(0.25, 0.05)

def avoid_lock(disable_lock=False):
    if disable_lock: return
    pyautogui.keyDown('ctrl')
    pyautogui.press('esc')
    pyautogui.keyUp('ctrl')
