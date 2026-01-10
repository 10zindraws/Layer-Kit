"""
    label-box adds a color label box on the layer docker like CSP
    Copyright (C) 2022  LunarKreatures

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

# For autocomplete
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .PyKrita import *
else:
    from krita import *

from PyQt5.QtCore import Qt, QSize, QPoint, QTimer
from PyQt5.QtWidgets import QHBoxLayout, QComboBox, QPushButton
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QPen

from .kritaUtils import getCurrentLayer, getSelectedLayers


# Colors for the color labels, copied from krita code in KisNodeViewColorScheme.cpp
transparentColor = QColor(Qt.transparent)  # 0
blueColor = QColor(91, 173, 220)          # 1
greenColor = QColor(151, 202, 63)         # 2
yellowColor = QColor(247, 229, 61)        # 3
orangeColor = QColor(255, 170, 63)        # 4
brownColor = QColor(177, 102, 63)         # 5
redColor = QColor(238, 50, 51)            # 6
purpleColor = QColor(191, 106, 209)       # 7
greyColor = QColor(118, 119, 114)         # 8


class LabelBox(Extension):
    def __init__(self, parent):
        super().__init__(parent)

        # Keep the QComboBox as the popup provider, but do not insert it into the UI.
        # A flat button is inserted instead, for a cleaner aesthetic.
        self.comboBox = self.buildComboBox()
        self.button = self.buildButton()

        application = Krita.instance()
        appNotifier = application.notifier()
        appNotifier.windowCreated.connect(self.addElement)

    # Krita.instance() exists, so do any setup work
    def setup(self):
        pass

    # called after setup(self)
    def createActions(self, window):
        pass

    def addElement(self):
        layerDocker = next((w for w in Krita.instance().dockers() if w.objectName() == 'KisLayerBox'), None)
        if layerDocker is None:
            return

        # The layout where the layer name is located
        layout = layerDocker.findChild(QHBoxLayout, 'hbox2')
        if layout is None:
            return

        # Insert button (not the combobox) for aesthetics
        layout.insertWidget(0, self.button)

        # Keep selection logic identical to the original plugin.
        self.comboBox.activated.connect(self.updateLayerColorLabel)

        # Connect to signals for automatic icon updates based on selected layer
        appNotifier = Krita.instance().notifier()
        appNotifier.imageCreated.connect(self.connectImageSignals)
        
        # Also connect to window signals for view/selection changes
        window = Krita.instance().activeWindow()
        if window:
            window.activeViewChanged.connect(self.onSelectionChanged)
        
        # Connect to existing documents
        for doc in Krita.instance().documents():
            self.connectDocumentSignals(doc)
        
        # Use a timer to poll for selection changes since Krita lacks a direct selection signal
        self.selectionTimer = QTimer()
        self.selectionTimer.timeout.connect(self.onSelectionChanged)
        self.selectionTimer.start(250)  # Check every 250ms
        
        # Initial update
        self.onSelectionChanged()

    def connectImageSignals(self, image):
        """Connect signals when a new image is created."""
        self.onSelectionChanged()

    def connectDocumentSignals(self, doc):
        """Connect signals for a document."""
        if not doc:
            return

        # Krita builds differ on which Document signals are exposed; guard for compatibility.
        node_inserted = getattr(doc, "nodeInserted", None)
        if node_inserted is not None and hasattr(node_inserted, "connect"):
            node_inserted.connect(self.onSelectionChanged)

        node_removed = getattr(doc, "nodeRemoved", None)
        if node_removed is not None and hasattr(node_removed, "connect"):
            node_removed.connect(self.onSelectionChanged)

    def onSelectionChanged(self):
        """Update the button icon based on the currently selected layer(s)."""
        try:
            selectedLayers = getSelectedLayers()
            
            # If multiple layers are selected, show transparent (index 0)
            if len(selectedLayers) > 1:
                self.button.setIcon(self.comboBox.itemIcon(0))
                return
            
            # Get the layer to check (either from selection or active node)
            if len(selectedLayers) == 1:
                layer = selectedLayers[0]
            else:
                layer = getCurrentLayer()
            
            if layer is None:
                self.button.setIcon(self.comboBox.itemIcon(0))
                return
            
            # Get the color label index and update the button icon
            colorLabelIndex = layer.colorLabel()
            # Ensure the index is valid (0-8)
            if 0 <= colorLabelIndex <= 8:
                self.button.setIcon(self.comboBox.itemIcon(colorLabelIndex))
            else:
                self.button.setIcon(self.comboBox.itemIcon(0))
        except Exception:
            # Silently fail if there's no active document or other issues
            pass

    def showPopup(self):
        # Position the (hidden) combobox directly under the button so the popup appears in the expected spot.
        pos = self.button.mapToGlobal(QPoint(0, self.button.height()))
        self.comboBox.move(pos)
        self.comboBox.showPopup()

    def updateLayerColorLabel(self, index: int):
        # Mirror current selection on the visible button.
        self.button.setIcon(self.comboBox.itemIcon(index))

        selectedLayers = getSelectedLayers()
        if len(selectedLayers) == 0:
            currentLayer = getCurrentLayer()
            currentLayer.setColorLabel(index)
        else:
            for layer in selectedLayers:
                layer.setColorLabel(index)

    def buildButton(self) -> QPushButton:
        btn = QPushButton()
        btn.setAccessibleName('colorLabelButton')
        btn.setObjectName('colorLabelButton')
        btn.setFixedSize(43, 32)
        btn.setFlat(True)

        btn.setIconSize(QSize(20, 20))
        btn.setIcon(self.comboBox.itemIcon(0))

        btn.clicked.connect(self.showPopup)
        return btn

    def buildComboBox(self) -> QComboBox:
        comboBox = QComboBox()
        comboBox.setAccessibleName('colorLabelBox')
        comboBox.setObjectName('colorLabelBox')
        comboBox.setFixedSize(43, 32)

        # generates an array to automatize the process of creating icons
        colors = [blueColor, greenColor, yellowColor, orangeColor, brownColor, redColor, purpleColor, greyColor]

        # I dont feel like figuring out how to make a transparent button like in the color labels
        # so i am going the csp route of just making an empty square
        transparentFill = QPixmap(20, 20)
        transparentFill.fill(transparentColor)

        # draws a rectangle around the transparent area
        painter = QPainter(transparentFill)
        pen = QPen()
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(0, 0, 20, 20)
        painter.end()
        transparentIcon = QIcon(transparentFill)
        comboBox.addItem(transparentIcon, '')

        # generates all icons
        for color in colors:
            colorFill = QPixmap(20, 20)
            colorFill.fill(color)
            colorIcon = QIcon(colorFill)
            comboBox.addItem(colorIcon, '')

        return comboBox
