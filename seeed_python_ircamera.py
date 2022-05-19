import sys
import threading
import seeed_mlx90640
from rpi_lcd import LCD
from gpiozero import DistanceSensor, LED, Buzzer, Button
from threading import Thread
from time import sleep, time
from subprocess import check_call
from signal import signal, SIGTERM, SIGHUP, pause
from serial import Serial
from PyQt5.QtWidgets import (
        QApplication,
        QGraphicsView,
        QGraphicsScene,
        QGraphicsPixmapItem,
        QGraphicsTextItem,
        QGraphicsEllipseItem,
        QGraphicsLineItem,
        QGraphicsBlurEffect,
    )
from PyQt5.QtGui import QPainter, QBrush, QColor, QFont, QPixmap
from PyQt5.QtCore import QThread, QObject, pyqtSignal, QPointF, Qt

lcd = LCD()
sensorEnter = DistanceSensor(echo = 20, trigger = 21)
sensorExit = DistanceSensor(echo = 12, trigger = 16)
buzzer = Buzzer(13)
ledWarn = LED(26)
ledEnter = LED(19)

def safe_exit(signum, frame):
    exit(1)

def mapValue(value, curMin, curMax, desMin, desMax):
    curDistance = value - curMax
    if curDistance == 0:
        return desMax
    curRange = curMax - curMin
    direction = 1 if curDistance > 0 else -1
    ratio = curRange / curDistance
    desRange = desMax - desMin
    value = desMax + (desRange / ratio)
    return value


def constrain(value, down, up):
    value = up if value > up else value
    value = down if value < down else value
    return value        


def isDigital(value):
    try:
        if value == "nan":
            return False
        else:
            float(value)
        return True
    except ValueError:
        return False

class TemperaturePrint():
    def __init__(self):
        self.temperature = 0
    
    def setTemp(self, temp):
        self.temperature = temp

class Distance():
    def __init__(self):
        self.enterDistance = 50
        self.exitDistance = 50
    
    def setEnterDistance(self, distance_enter):
        self.enterDistance = distance_enter
    
    def setExitDistance(self, distance_exit):
        self.exitDistance = distance_exit
    
class Count():
    def __init__(self):
        self.count = 0
    
    def increment(self):
        self.count += 1
    
    def decrement(self):
        self.count -= 1
        if self.count < 0:
            self.count = 0

hetaData = []
lock = threading.Lock()

# Settings
minHue = 180
maxHue = 360
required_distance = 0.45
reading = True
offset_temp = 1.5
fever = 38.0
# normal_temp = 35.0
max_capacity = 10

# Data class initialize
count = Count()
temperature_print = TemperaturePrint()

class DataReader(QThread):
    drawRequire = pyqtSignal()

    I2C = 0,
    SERIAL = 1
    MODE = I2C

    def __init__(self, port):
        super(DataReader, self).__init__()
        self.frameCount = 0
        # i2c mode
        if port is None:
            self.dataHandle = seeed_mlx90640.grove_mxl90640()
            self.dataHandle.refresh_rate = seeed_mlx90640.RefreshRate.REFRESH_4_HZ
            self.readData = self.i2cRead
        else:
            self.MODE = DataReader.SERIAL
            self.port = port
            self.dataHandle = Serial(self.port, 2000000, timeout=5)
            self.readData = self.serialRead

    def i2cRead(self):
        hetData = [0]*768
        self.dataHandle.getFrame(hetData)
        return hetData

    def serialRead(self):
        hetData = self.dataHandle.read_until(terminator=b'\r\n')
        hetData = str(hetData, encoding="utf8").split(",")
        hetData = hetData[:-1]
        return hetData

    def run(self):
        # throw first frame
        self.readData()
        while True:
            maxHet = 0
            minHet = 500
            tempData = []
            nanCount = 0

            hetData = self.readData()

            if  len(hetData) < 768 :
                continue

            for i in range(0, 768):
                curCol = i % 32
                newValueForNanPoint = 0
                curData = None

                if i < len(hetData) and isDigital(hetData[i]):
                    curData = float(hetData[i])
                else:
                    interpolationPointCount = 0
                    sumValue = 0
                    # print("curCol",curCol,"i",i)

                    abovePointIndex = i-32
                    if (abovePointIndex>0):
                        if hetData[abovePointIndex] is not "nan" :
                            interpolationPointCount += 1
                            sumValue += float(hetData[abovePointIndex])

                    belowPointIndex = i+32
                    if (belowPointIndex<768):
                        print(" ")
                        if hetData[belowPointIndex] is not "nan" :
                            interpolationPointCount += 1
                            sumValue += float(hetData[belowPointIndex])
                            
                    leftPointIndex = i -1
                    if (curCol != 31):
                        if hetData[leftPointIndex]  is not "nan" :
                            interpolationPointCount += 1
                            sumValue += float(hetData[leftPointIndex])

                    rightPointIndex = i + 1
                    if (belowPointIndex<768):
                        if (curCol != 0):
                            if hetData[rightPointIndex] is not "nan" :
                                interpolationPointCount += 1
                                sumValue += float(hetData[rightPointIndex])

                    curData =  sumValue /interpolationPointCount
                    # For debug :
                    # print(abovePointIndex,belowPointIndex,leftPointIndex,rightPointIndex)
                    # print("newValueForNanPoint",newValueForNanPoint," interpolationPointCount" , interpolationPointCount ,"sumValue",sumValue)
                    nanCount +=1

                tempData.append(curData)
                maxHet = tempData[i] if tempData[i] > maxHet else maxHet
                minHet = tempData[i] if tempData[i] < minHet else minHet

            if maxHet == 0 or minHet == 500:
                continue
            # For debug :
            # if nanCount > 0 :
            #     print("____@@@@@@@ nanCount " ,nanCount , " @@@@@@@____")
           
            lock.acquire()
            hetaData.append(
                {
                    "frame": tempData,
                    "maxHet": maxHet,
                    "minHet": minHet
                }
            )
            lock.release()
            self.drawRequire.emit()
            self.frameCount = self.frameCount + 1
            #print("data->" + str(self.frameCount))
        self.com.close()

class painter(QGraphicsView):
    narrowRatio = int(sys.argv[4]) if len(sys.argv) >= 5 else 1
    useBlur = sys.argv[5] != "False" if len(sys.argv) >= 6 else True
    pixelSize = int(15 / narrowRatio)
    width = int (480 / narrowRatio)
    height = int(360 / narrowRatio)

    fontSize = int(30 / narrowRatio) + 10
    cneterFontSize = int(30 / narrowRatio) + 15

    anchorLineSize = int(100 / narrowRatio)
    ellipseRadius = int(8 / narrowRatio)
    textInterval = int(90 / narrowRatio)
    col = width / pixelSize
    line = height / pixelSize
    centerIndex = int(round(((line / 2 - 1) * col) + col / 2))
    frameCount = 0
    baseZValue = 0
    textLineHeight = fontSize + 10
    blurRaduis = 50  # Smoother improvement

    # Initially, timer counter must be 3
    timer_counter = 3

    # Initially, temp_temperature must be a space
    # Also it MUST ALWAYS be a string
    temp_temperature: str = " "

    # Initialize timer stop flag
    timerStop = False

    # Initialize start_timer flag
    start_timer = time()

    def __init__(self):
        super(painter, self).__init__()
        self.setFixedSize(self.width, self.height + self.textLineHeight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scene = QGraphicsScene()
        self.setScene(self.scene)

        # center het text item
        self.centerTextItem = QGraphicsTextItem()
        self.centerTextItem.setPos(self.width / 2 - self.cneterFontSize, 0)
        self.centerTextItem.setZValue(self.baseZValue + 1)
        self.scene.addItem(self.centerTextItem)

        # timer TEXT item
        self.timerItem = QGraphicsTextItem()
        self.timerItem.setPos(self.width - self.fontSize - 50, 0)
        self.timerItem.setZValue(self.baseZValue + 1)
        self.scene.addItem(self.timerItem)

        # timer item
        self.timerTextItem = QGraphicsTextItem()
        self.timerTextItem.setPos(self.width - self.fontSize - 10, self.fontSize + 10)
        self.timerTextItem.setZValue(self.baseZValue + 1)
        self.scene.addItem(self.timerTextItem)

        # center anchor item
        centerX = self.width / 2
        centerY = self.height / 2
        self.ellipseItem = QGraphicsEllipseItem(
                0, 0, 
                self.ellipseRadius * 2, 
                self.ellipseRadius * 2
            )
        self.horLineItem = QGraphicsLineItem(0, 0, self.anchorLineSize, 0)
        self.verLineItem = QGraphicsLineItem(0, 0, 0, self.anchorLineSize)
        self.ellipseItem.setPos(
                centerX - self.ellipseRadius, 
                centerY - self.ellipseRadius
            )
        self.horLineItem.setPos(centerX - self.anchorLineSize / 2, centerY)
        self.verLineItem.setPos(centerX, centerY - self.anchorLineSize / 2)
        self.ellipseItem.setPen(QColor(Qt.white))
        self.horLineItem.setPen(QColor(Qt.white))
        self.verLineItem.setPen(QColor(Qt.white))
        self.ellipseItem.setZValue(self.baseZValue + 1)
        self.horLineItem.setZValue(self.baseZValue + 1)
        self.verLineItem.setZValue(self.baseZValue + 1)
        self.scene.addItem(self.ellipseItem)
        self.scene.addItem(self.horLineItem)
        self.scene.addItem(self.verLineItem)
        # camera item
        self.cameraBuffer = QPixmap(self.width, self.height + self.textLineHeight)
        self.cameraItem = QGraphicsPixmapItem()
        if self.useBlur:
            self.gusBlurEffect = QGraphicsBlurEffect()
            self.gusBlurEffect.setBlurRadius(self.blurRaduis)
            self.cameraItem.setGraphicsEffect(self.gusBlurEffect)
        self.cameraItem.setPos(0, 0)
        self.cameraItem.setZValue(self.baseZValue)
        self.scene.addItem(self.cameraItem)
        # het text item
        self.hetTextBuffer = QPixmap(self.width, self.textLineHeight)
        self.hetTextItem = QGraphicsPixmapItem()
        self.hetTextItem.setPos(0, self.height)
        self.hetTextItem.setZValue(self.baseZValue)
        self.scene.addItem(self.hetTextItem)

    def draw(self):
        if len(hetaData) == 0:
            return
        font = QFont()
        color = QColor()

        font.setPointSize(self.fontSize)
        font.setFamily("Microsoft YaHei")
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0)

        timerFont = QFont()
        timerFont.setPointSize(self.cneterFontSize - 20)
        timerFont.setFamily("Microsoft YaHei")
        timerFont.setLetterSpacing(QFont.AbsoluteSpacing, 0)

        cneterFont = QFont()
        cneterFont.setPointSize(self.cneterFontSize)
        cneterFont.setFamily("Microsoft YaHei")
        cneterFont.setLetterSpacing(QFont.AbsoluteSpacing, 0)

        index = 0
        lock.acquire()
        frame = hetaData.pop(0)
        lock.release()
        maxHet = frame["maxHet"]
        minHet = frame["minHet"]
        frame = frame["frame"]
        p = QPainter(self.cameraBuffer)
        p.fillRect(
                0, 0, self.width, 
                self.height + self.textLineHeight, 
                QBrush(QColor(Qt.black))
            )
        # draw camera
        color = QColor()
        for yIndex in range(int(self.height / self.pixelSize)):
            for xIndex in range(int(self.width / self.pixelSize)):
                tempData = constrain(mapValue(frame[index], minHet, maxHet, minHue, maxHue), minHue, maxHue)
                color.setHsvF(tempData / 360, 1.0, 1.0)
                p.fillRect(
                    xIndex * self.pixelSize,
                    yIndex * self.pixelSize,
                    self.pixelSize, self.pixelSize,
                    QBrush(color)
                )
                index = index + 1
        self.cameraItem.setPixmap(self.cameraBuffer)

        # draw text
        p = QPainter(self.hetTextBuffer)

        hetDiff = maxHet - minHet
        bastNum = round(minHet)
        interval = round(hetDiff / 5)

        # Get temperature of center pixel
        cneter = round(frame[self.centerIndex], 1) + offset_temp

        # Check if there is someone entering
        if(sensorEnter.value >= required_distance):
            bgcolor = Qt.white
            textDisplay = "Go near entrance"
            self.start_timer = time()
            self.timerStop = False
            self.timer_counter = 3
            self.temp_temperature = " "
        else:
            if((time() - self.start_timer) >= 1) and not self.timerStop:
                print(time())
                print(self.timer_counter)
                self.start_timer = time()
                self.timer_counter -= 1
                if self.timer_counter == 0:
                    self.temp_temperature = str(cneter) + "°"
                    self.timerStop = True
                    temperature_print.setTemp(cneter)
            if self.timerStop:
                # See if the temperature will allow the user to enter or not
                if (temperature_print.temperature > fever):
                    bgcolor = Qt.red
                    textDisplay = "Entrance denied."
                else:
                    bgcolor = Qt.green
                    textDisplay = "Please enter."
            else:
                bgcolor = Qt.white
                textDisplay = "Timer must be 0"

        # Logic for printing the background and text
        p.fillRect(
                0, 0, self.width, 
                self.height + self.textLineHeight, 
                QBrush(QColor(bgcolor))
        )
        color.setHsvF(0.0, 1.0, 0.0)
        p.setPen(color)
        p.setFont(font)
        p.drawText(3, self.fontSize + 3, textDisplay)
        
        self.hetTextItem.setPixmap(self.hetTextBuffer)

        # draw timer text item
        timerActualText = "<font color=white>%s</font>"
        self.timerItem.setFont(timerFont)
        self.timerItem.setHtml(timerActualText % "Timer:")

        # draw timer text item
        timerText = "<font color=white>%s</font>"
        self.timerTextItem.setFont(font)
        self.timerTextItem.setHtml(timerText % (str(self.timer_counter)))

        # draw center het text
        centerText = "<font color=white>%s</font>"
        self.centerTextItem.setFont(cneterFont)
        self.centerTextItem.setHtml(centerText % (str(self.temp_temperature)))

        # Increase frame count then print to cmd line
        self.frameCount = self.frameCount + 1
        #print("picture->"+str(self.frameCount))

        # write in lcd
        # temperature_print.setTemp(cneter)
    
def run():
    global minHue
    global maxHue
    if len(sys.argv) >= 2 and sys.argv[1] == "-h":
        print("Usage: %s [PortName] [minHue] [maxHue] [NarrowRatio] [UseBlur]" % sys.argv[0])
        exit(0)
    if len(sys.argv) >= 4:
        minHue = int(sys.argv[2])
        maxHue = int(sys.argv[3])
    if len(sys.argv) >= 2:
        port = sys.argv[1]
    else:
        port = None
    app = QApplication(sys.argv)
    window = painter()
    dataThread = DataReader(port)
    dataThread.drawRequire.connect(window.draw)
    dataThread.start()
    window.show()    
    app.exec_()

def read_temperature():
    while reading:
        lcd.text("Counter: " + str(count.count), 1)
        if(count.count >= max_capacity):
            lcd.text("Already full!", 2)
        else:
            lcd.text("                ",2)
        sleep(0.2)

def counter():
    exitDetected = False
    enterDetected = False
    hasFever = False
    beepActive = False
    previousBeepActive = False
    countUp = False
    buzzerOff = False
    previousBuzzerOff = False
    beepFever = False
    previousBeepFever = False
    beepExit = False
    previousBeepExit = False
    beepFull = True

    start_time = time()
    timer_count = 3
    timerStop = False
    
    while reading:
        exitDistance = sensorExit.value
        enterDistance = sensorEnter.value
        print("SensorExit: "  + '{:1.2f}'.format(exitDistance) + " cm")
        print("SensorEnter: "  + '{:1.2f}'.format(enterDistance) + " cm")

        if(enterDistance >= required_distance):
            start_time = time()
            timer_count = 3
            timerStop = False

        else:
            if((time() - start_time) >= 1) and not timerStop:
                start_time = time()
                timer_count -= 1
                if timer_count == 0:
                    timerStop = True

        if timerStop:
            if temperature_print.temperature > fever:
                ledWarn.on()
                buzzer.on()
            else:
                ledEnter.on()
                beepActive = True

            if not previousBeepActive and beepActive:
                buzzer.beep(on_time=0.3, n=1)
                count.increment()
                previousBeepActive = True
        else:
            beepActive = False
            previousBeepActive = False
            if not beepExit or not beepActive and not beepFull:
                buzzer.off()
                ledWarn.off()
            ledEnter.off()

        if count.count >= 10:
            beepFull = True
            ledWarn.on()
            buzzer.on()
        else:
            beepFull = False


        # Exit Logic
        if (exitDistance <= required_distance):
            exitDetected = True
            beepExit = True

        if not previousBeepExit and beepExit:
            buzzer.beep(on_time = 0.3, n=1)
            previousBeepExit = True
            count.decrement()

        if (exitDetected and (exitDistance > required_distance)):
            exitDetected = False
            beepExit = False
            previousBeepExit = False


        sleep(0.2)

def shutdown():
    check_call(['sudo','poweroff'])
        
signal(SIGTERM, safe_exit)
signal(SIGHUP, safe_exit)

try:
    thermal_imaging = Thread(target=run, daemon=True)
    reader = Thread(target=read_temperature, daemon=True)
    counter_check = Thread(target=counter, daemon=True)
    shutdown_btn = Button(6, hold_time=2)
    thermal_imaging.start()
    reader.start()
    counter_check.start()
    shutdown_btn.when_held = shutdown
    
    pause()
    
except KeyboardInterrupt:
    pass

except ValueError:
    pass

finally:
    reading = False
    sleep(0.5)
    lcd.clear()
    sensorEnter.close()
    sensorExit.close()
    ledWarn.close()
    ledEnter.close()
