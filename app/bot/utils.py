import time
import random
import math
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

def bezier_curve(start, end, n_points=20):
    """
    Generates n_points along a cubic Bezier curve for smooth movement.
    Control points are randomized to create an arc.
    """
    x1, y1 = start
    x2, y2 = end
    
    # Distance between points
    dist = math.hypot(x2 - x1, y2 - y1)
    
    # Randomize control points based on distance
    # We want the curve to be somewhat direct but human-like (not a straight line)
    
    # Control Point 1
    ctrl1_x = x1 + (x2 - x1) * 0.3 + random.uniform(-50, 50)
    ctrl1_y = y1 + (y2 - y1) * 0.3 + random.uniform(-50, 50)
    
    # Control Point 2
    ctrl2_x = x1 + (x2 - x1) * 0.7 + random.uniform(-50, 50)
    ctrl2_y = y1 + (y2 - y1) * 0.7 + random.uniform(-50, 50)
    
    path = []
    # Pure Python linspace equivalent
    steps = [i / (n_points - 1) for i in range(n_points)]
    
    for t in steps:
        # Cubic Bezier Formula
        x = (1-t)**3 * x1 + 3*(1-t)**2 * t * ctrl1_x + 3*(1-t) * t**2 * ctrl2_x + t**3 * x2
        y = (1-t)**3 * y1 + 3*(1-t)**2 * t * ctrl1_y + 3*(1-t) * t**2 * ctrl2_y + t**3 * y2
        path.append((x, y))
        
    return path

def human_mouse_move(browser, element):
    """
    Moves the virtual mouse to the element using a generated Bezier curve path.
    This uses Selenium ActionChains, so it is non-invasive (does not hijack system mouse).
    """
    try:
        # ActionChains typically works with relative movements or direct moves.
        # Direct move_to_element is straight.
        # To simulate a curve, we would need to move by small offsets.
        # However, standard Selenium ActionChains `move_by_offset` is relative to current position.
        # Getting absolute current mouse position in Selenium is tricky without external tools.
        #
        # ALTERNATIVE STRATEGY for Reliability + Simplicity:
        # Instead of a full path simulation (which can be flaky in headless/undetected),
        # we will break the movement into 2-3 "micro-moves" towards the target to simulate
        # a non-instant jump, followed by a small pause.
        
        actions = ActionChains(browser)
        
        # 1. Move to a random offset slightly OFF the element first (hover intent)
        x_offset = random.randint(-40, 40)
        y_offset = random.randint(-40, 40)
        
        actions.move_to_element_with_offset(element, x_offset, y_offset).perform()
        time.sleep(random.uniform(0.1, 0.3))
        
        # 2. Correct to the center of the element
        actions.move_to_element(element).perform()
        time.sleep(random.uniform(0.1, 0.2))
        
        return actions
        
    except Exception:
        # Fallback
        return ActionChains(browser).move_to_element(element)

def smart_click(browser, element):
    """
    Moves the virtual cursor to the element with small human-like jitter/steps, pauses, and clicks.
    """
    try:
        human_mouse_move(browser, element)
        time.sleep(random.uniform(0.1, 0.3))
        # Click
        ActionChains(browser).click().perform()
    except Exception:
        # Robust fallback
        try:
            element.click()
        except:
            browser.execute_script("arguments[0].click();", element)

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



