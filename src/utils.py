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
    # Ensure sleep less than 1 second to be safe
    sleep_time = max(1.0, sleep_time)
    time.sleep(sleep_time)

def human_type(element, text, min_delay=0.05, max_delay=0.2):
    """
    Types text into an element character-by-character with random delays,
    mimicking human typing speed. Safe for multitasking (targets element only).
    """
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))
        if random.random() < 0.1:
            time.sleep(random.uniform(0.1, 0.4))

def smart_click(browser, element):
    """
    Moves the virtual cursor to the element with small jitter, pauses, and clicks.
    """
    try:
        actions = ActionChains(browser)
        actions.move_to_element(element).perform()
        human_sleep(0.3, 0.1)
        actions.click().perform()
    except Exception:
        element.click()

def scroll_slow(browser, scrollable_element, start=0, end=3600, step=100, reverse=False):
    if reverse:
        start, end = end, start
        step = -step

    current_pos = start
    # Dynamic scrolling
    while (step > 0 and current_pos < end) or (step < 0 and current_pos > end):
        variance = random.randint(-20, 20)
        current_step = step + variance
        current_pos += current_step
        
        # Clamp
        if step > 0: current_pos = min(current_pos, end)
        else: current_pos = max(current_pos, end)

        browser.execute_script("arguments[0].scrollTo(0, {})".format(current_pos), scrollable_element)
        
        if random.random() < 0.2:
            time.sleep(random.uniform(0.3, 0.6))
        else:
            time.sleep(random.uniform(0.05, 0.15))

def avoid_lock(disable_lock=False):
    if disable_lock: return
    pyautogui.keyDown('ctrl')
    pyautogui.press('esc')
    pyautogui.keyUp('ctrl')
