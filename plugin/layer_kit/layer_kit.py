# Layer Kit — Krita Python Plugin (Krita 5.2+)
#
# This plugin patches the existing built-in "Layers" docker (KisLayerBox)
# by locating its internal Qt widgets/layouts and inserting/hiding controls.
#
# Layout structure from Krita's WdgLayerBox.ui:
# - hbox1: Bottom bar with bnAdd, bnDuplicate, bnLower, bnRaise, bnProperties, spacer, bnDelete
# - hbox2: Top bar with cmbComposite (blending mode), bnLayerFilters
# - opacityLayout: Opacity row with opacityLabel, doubleOpacity, configureLayerDockerToolbar
# - horizontalLayout: Contains listLayers (NodeView)

from __future__ import annotations

import os
from typing import Optional, List, Dict, Tuple

from krita import Extension, Krita

from PyQt5.QtCore import Qt, QTimer, QObject, QSize
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QDockWidget,
    QToolButton,
    QHBoxLayout,
    QDoubleSpinBox,
    QTreeView,
    QAbstractItemView,
    QWidget,
)
from PyQt5.QtCore import QItemSelectionModel


def _plugin_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _icon_path(filename: str) -> str:
    return os.path.join(_plugin_dir(), "icons", filename)


def _load_icon(filename: str) -> QIcon:
    path = _icon_path(filename)
    if os.path.exists(path):
        return QIcon(path)
    return QIcon()


def _safe_set_visible(w: Optional[QWidget], visible: bool) -> None:
    if w is None:
        return
    w.setVisible(visible)
    w.setEnabled(visible)


def _find_action_by_ids(action_ids: List[str]):
    """Return first QAction found by id among action_ids, else None."""
    inst = Krita.instance()
    for aid in action_ids:
        try:
            act = inst.action(aid)
        except Exception:
            act = None
        if act is not None:
            return act
    return None


def _find_action_by_text(needles: List[str]):
    """
    Best-effort action lookup by visible text.
    needles: list of substrings that must all appear (case-insensitive) in action.text().
    """
    inst = Krita.instance()
    actions = None
    try:
        actions = inst.actions()
    except Exception:
        actions = None
    if not actions:
        return None

    needles_l = [n.lower() for n in needles]
    for act in actions:
        try:
            text = (act.text() or "").lower()
        except Exception:
            continue
        if all(n in text for n in needles_l):
            return act
    return None


class GroupLayerLabeler(QObject):
    """
    Monitors the document and automatically applies Krita's color label
    to group layers. Uses Krita's native setColorLabel() method.
    
    Color label indices in Krita:
    0 = None (no color)
    1 = Blue
    2 = Green  
    3 = Yellow
    4 = Orange
    5 = Brown
    6 = Red
    7 = Purple
    8 = Grey
    """
    
    # Default color label for group layers (8 = Grey)
    GROUP_LABEL_COLOR = 8
    
    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._labeled_nodes: set = set()  # Track nodes we've already labeled by uniqueId
        self._timer: Optional[QTimer] = None
    
    def start_monitoring(self) -> None:
        """Start periodic monitoring for new group layers."""
        if self._timer is not None:
            return
        
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_and_label_groups)
        self._timer.start(1000)  # Check every second
        
        # Also do an immediate check
        QTimer.singleShot(100, self._check_and_label_groups)
    
    def stop_monitoring(self) -> None:
        """Stop monitoring."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
    
    def _check_and_label_groups(self) -> None:
        """Check all documents and label any unlabeled group layers."""
        try:
            app = Krita.instance()
            docs = app.documents()
            if not docs:
                return
            
            for doc in docs:
                self._label_groups_in_document(doc)
        except Exception:
            pass
    
    def _label_groups_in_document(self, doc) -> None:
        """Recursively find and label group layers in a document."""
        if doc is None:
            return
        
        try:
            root = doc.rootNode()
            if root is None:
                return
            self._process_node_tree(root, doc)
        except Exception:
            pass
    
    def _process_node_tree(self, node, doc) -> None:
        """Recursively process nodes and label groups."""
        if node is None:
            return
        
        try:
            children = node.childNodes() if callable(getattr(node, "childNodes", None)) else []
        except Exception:
            children = []
        
        for child in children:
            try:
                # Check if this is a group layer (not a group mask)
                node_type = child.type() if callable(getattr(child, "type", None)) else ""
                node_type_lower = (node_type or "").lower()
                
                is_group = ("group" in node_type_lower) and ("mask" not in node_type_lower)
                
                if is_group:
                    # Get unique identifier for tracking
                    node_uid = None
                    if callable(getattr(child, "uniqueId", None)):
                        try:
                            node_uid = child.uniqueId()
                        except Exception:
                            node_uid = None
                    
                    # Use name + type as fallback identifier
                    if node_uid is None:
                        try:
                            node_name = child.name() if callable(getattr(child, "name", None)) else ""
                            node_uid = f"{node_name}_{node_type}"
                        except Exception:
                            node_uid = id(child)
                    
                    # Check if already labeled by us
                    if node_uid not in self._labeled_nodes:
                        # Check current color label
                        current_label = 0
                        if callable(getattr(child, "colorLabel", None)):
                            try:
                                current_label = child.colorLabel()
                            except Exception:
                                current_label = 0
                        
                        # Only set label if not already set (0 = no color)
                        if current_label == 0:
                            if callable(getattr(child, "setColorLabel", None)):
                                try:
                                    child.setColorLabel(self.GROUP_LABEL_COLOR)
                                    self._labeled_nodes.add(node_uid)
                                except Exception:
                                    pass
                        else:
                            # Already has a label, just track it
                            self._labeled_nodes.add(node_uid)
                
                # Recurse into children (groups can contain groups)
                self._process_node_tree(child, doc)
                
            except Exception:
                continue


class LayersDockerPatcher(QObject):
    """
    Encapsulates patching logic for one KisLayerBox docker instance.
    Uses layout names from Krita's WdgLayerBox.ui for reliable widget insertion.
    """

    def __init__(self, docker: QDockWidget, parent: QObject = None):
        super().__init__(parent)
        self.docker = docker
        self.tree: Optional[QTreeView] = None
        self._labeler: Optional[GroupLayerLabeler] = None
        self._patched_buttons = set()  # Track what we've already added

    def apply(self) -> None:
        self._add_group_button()
        self._add_clipping_mask_button()
        self._install_group_layer_labeling()

    def _add_group_button(self) -> None:
        """Add the 'Add Group Layer' button to the right of bnAdd in hbox1 (bottom bar)."""
        btn_name = "btnAddGroupLayer_LayerKit"
        
        # Check if already added
        if btn_name in self._patched_buttons:
            return
        existing = self.docker.findChild(QToolButton, btn_name)
        if existing is not None:
            self._patched_buttons.add(btn_name)
            return

        # Find the bottom bar layout by name (hbox1 from WdgLayerBox.ui)
        hbox1 = self.docker.findChild(QHBoxLayout, "hbox1")
        
        # Find bnAdd for reference
        bn_add = self.docker.findChild(QToolButton, "bnAdd")
        if bn_add is None:
            return
            
        # If layout not found by name, get it from bnAdd's parent
        if hbox1 is None:
            parent_w = bn_add.parentWidget()
            if parent_w is None:
                return
            hbox1 = parent_w.layout()
            if hbox1 is None:
                return

        add_index = self._index_of_widget(hbox1, bn_add)
        
        # Get the parent widget for the new button
        parent_widget = bn_add.parentWidget()
        if parent_widget is None:
            return

        btn = QToolButton(parent_widget)
        btn.setObjectName(btn_name)
        btn.setAutoRaise(True)
        btn.setIcon(_load_icon("folder.png"))
        btn.setIconSize(QSize(20, 20))
        btn.setToolTip("Add Group Layer")
        btn.setMinimumSize(28, 28)

        def on_click():
            act = _find_action_by_ids([
                "add_new_group_layer",
                "add_group_layer",
                "layer_add_group",
                "add_group",
            ])
            if act is None:
                act = _find_action_by_text(["add", "group", "layer"])
            if act is not None:
                act.trigger()

        btn.clicked.connect(on_click)

        # Insert right after bnAdd (index + 1)
        if add_index != -1:
            hbox1.insertWidget(add_index + 1, btn)
        else:
            # Fallback: insert at position 1
            hbox1.insertWidget(1, btn)
        
        self._patched_buttons.add(btn_name)

    def _add_clipping_mask_button(self) -> None:
        """Add the 'Clipping Mask' button to the left of the opacity slider in opacityLayout."""
        btn_name = "btnClippingMask_LayerKit"
        
        # Check if already added
        if btn_name in self._patched_buttons:
            return
        existing = self.docker.findChild(QToolButton, btn_name)
        if existing is not None:
            self._patched_buttons.add(btn_name)
            return

        # Find the opacity layout by name (opacityLayout from WdgLayerBox.ui)
        opacity_layout = self.docker.findChild(QHBoxLayout, "opacityLayout")
        
        # Find doubleOpacity for reference
        spin = self.docker.findChild(QDoubleSpinBox, "doubleOpacity")
        if spin is None:
            # Try soft search
            for cand in self.docker.findChildren(QDoubleSpinBox):
                if "opacity" in (cand.objectName() or "").lower():
                    spin = cand
                    break
        if spin is None:
            return
        
        # If layout not found by name, get it from spin's parent
        if opacity_layout is None:
            parent_w = spin.parentWidget()
            if parent_w is None:
                return
            opacity_layout = parent_w.layout()
            if opacity_layout is None:
                return

        spin_index = self._index_of_widget(opacity_layout, spin)
        
        # Get parent widget for button
        parent_widget = spin.parentWidget()
        if parent_widget is None:
            return

        btn = QToolButton(parent_widget)
        btn.setObjectName(btn_name)
        btn.setAutoRaise(True)
        btn.setIcon(_load_icon("clippingmask.png"))
        btn.setIconSize(QSize(22, 22))
        btn.setToolTip("Clipping Mask (Alpha Inheritance + Quick Group)")
        btn.setMinimumSize(28, 28)
        btn.setMaximumSize(32, 32)

        btn.clicked.connect(self._do_clipping_mask_sequence)

        # Insert before the opacity slider
        if spin_index != -1:
            opacity_layout.insertWidget(spin_index, btn)
        else:
            opacity_layout.insertWidget(0, btn)
        
        self._patched_buttons.add(btn_name)

    def _has_layer_below(self, node) -> bool:
        """
        Check if there is a layer/group below the given node in the layer stack.
        Uses the tree view's model to check for siblings below.
        
        Returns True if there's a layer below, False otherwise.
        """
        if self.tree is None:
            self.tree = self._find_layer_tree_view()
        if self.tree is None:
            return False
        
        try:
            sm = self.tree.selectionModel()
            if sm is None:
                return False
            
            current = sm.currentIndex()
            if not current.isValid():
                current = self.tree.currentIndex()
            if not current.isValid():
                return False
            
            model = current.model()
            parent = current.parent()
            row = current.row()
            
            # In Krita's layer view, row+1 is the layer below (toward higher indices)
            below = model.index(row + 1, current.column(), parent)
            if not below.isValid():
                below = model.index(row + 1, 0, parent)
            
            return below.isValid()
        except Exception:
            return False

    def _do_clipping_mask_sequence(self) -> None:
        """
        Implements:
          1) Check if there is a layer below the current node (abort if not)
          2) Enable inherit alpha on current node (using Krita's action for undo support)
          3) Add layer below to selection (multi-select)
          4) Quick Group the two layers
          5) Ensure the top (clipped) layer is the active/selected layer (only)
        """
        app = Krita.instance()
        doc = app.activeDocument()
        if doc is None:
            return
        try:
            node = doc.activeNode()
        except Exception:
            node = None
        if node is None:
            return

        # Ensure we have the tree view for selection manipulation (needed for layer below check)
        if self.tree is None:
            self.tree = self._find_layer_tree_view()

        # 0) Check if there is a layer below the current node
        # If there's no layer below, the clipping mask cannot work, so we abort
        if not self._has_layer_below(node):
            # No layer below - cannot create clipping mask, silently abort
            return

        # Store node info for finding it later
        node_name = None
        node_type = None
        try:
            node_name = node.name() if callable(getattr(node, "name", None)) else None
            node_type = node.type() if callable(getattr(node, "type", None)) else None
        except Exception:
            pass

        # 1) Turn on alpha inheritance for current node
        # Use Krita's built-in action for proper undo support via KisNodePropertyListCommand
        self._trigger_inherit_alpha_action(node)

        # 2) Add the layer below as selected
        self._select_below_additive()

        # 3) Quick group selected layers
        self._trigger_quick_group_action()

        # 4) Restore active node selection to the original top layer
        def restore_selection():
            try:
                doc_now = app.activeDocument()
                if doc_now is None:
                    return
                
                target_node = None
                
                # Try to find the node by name
                if node_name is not None:
                    target_node = self._find_node_by_name_and_type(doc_now, node_name, node_type)
                
                if target_node is not None:
                    # Set as active node
                    self._set_active_node(doc_now, target_node)
                    
                    # Clear selection and select only this node
                    if self.tree is not None:
                        sm = self.tree.selectionModel()
                        if sm is not None:
                            sm.clearSelection()
                    
                    self._select_only_node_in_view(target_node)
                    self._set_active_node(doc_now, target_node)
                
                if self.tree is not None:
                    self.tree.viewport().update()
            except Exception:
                pass

        QTimer.singleShot(150, restore_selection)

    def _trigger_quick_group_action(self) -> bool:
        act = _find_action_by_ids([
            "create_quick_group",
            "quick_group",
            "quick_group_layers",
            "layer_quick_group",
            "group_layers",
        ])
        if act is None:
            act = _find_action_by_text(["quick", "group"])
        if act is None:
            act = _find_action_by_text(["group", "layers"])
        if act is None:
            return False
        try:
            act.trigger()
            return True
        except Exception:
            return False

    def _trigger_inherit_alpha_action(self, node) -> bool:
        """
        Enable inherit alpha on the given node using Krita's built-in action.
        This ensures proper undo support via KisNodePropertyListCommand.
        
        The action toggles the state, so we first check current state
        and only trigger if alpha inheritance is not already enabled.
        """
        # Check if alpha inheritance is already enabled
        already_enabled = False
        try:
            if callable(getattr(node, "inheritAlpha", None)):
                already_enabled = node.inheritAlpha()
        except Exception:
            pass
        
        if already_enabled:
            # Already enabled, no need to toggle
            return True
        
        # Try to use Krita's built-in action for proper undo support
        act = _find_action_by_ids([
            "toggle_layer_inherit_alpha",
            "toggle_inherit_alpha", 
            "inherit_alpha",
            "layer_inherit_alpha",
        ])
        if act is None:
            act = _find_action_by_text(["inherit", "alpha"])
        
        if act is not None:
            try:
                act.trigger()
                return True
            except Exception:
                pass
        
        # Fallback: direct call (no undo support, but better than nothing)
        try:
            if callable(getattr(node, "setInheritAlpha", None)):
                node.setInheritAlpha(True)
                return True
        except Exception:
            pass
        
        return False

    def _set_active_node(self, doc, node) -> None:
        try:
            if callable(getattr(doc, "setActiveNode", None)):
                doc.setActiveNode(node)
                return
        except Exception:
            pass
        try:
            view = Krita.instance().activeWindow().activeView()
        except Exception:
            view = None
        if view is not None:
            try:
                if callable(getattr(view, "setCurrentNode", None)):
                    view.setCurrentNode(node)
            except Exception:
                pass

    def _find_node_by_name_and_type(self, doc, name: str, node_type: Optional[str]):
        """Recursively search for a node matching the given name and type."""
        if doc is None or name is None:
            return None
        
        def search_nodes(parent_node):
            if parent_node is None:
                return None
            try:
                children = parent_node.childNodes() if callable(getattr(parent_node, "childNodes", None)) else []
            except Exception:
                children = []
            
            for child in children:
                try:
                    child_name = child.name() if callable(getattr(child, "name", None)) else None
                    child_type = child.type() if callable(getattr(child, "type", None)) else None
                except Exception:
                    continue
                
                if child_name == name:
                    if node_type is None or child_type == node_type:
                        return child
                
                # Recursively search in groups
                try:
                    child_type_lower = (child_type or "").lower()
                except Exception:
                    child_type_lower = ""
                
                if "group" in child_type_lower:
                    result = search_nodes(child)
                    if result is not None:
                        return result
            
            return None
        
        try:
            root = doc.rootNode() if callable(getattr(doc, "rootNode", None)) else None
            return search_nodes(root)
        except Exception:
            return None

    def _select_below_additive(self) -> bool:
        if self.tree is None:
            return False
        try:
            sm = self.tree.selectionModel()
        except Exception:
            return False
        if sm is None:
            return False

        current = sm.currentIndex()
        if not current.isValid():
            current = self.tree.currentIndex()
        if not current.isValid():
            return False

        model = current.model()
        parent = current.parent()
        row = current.row()
        below = model.index(row + 1, current.column(), parent)
        if not below.isValid():
            below = model.index(row + 1, 0, parent)
        if not below.isValid():
            return False

        try:
            sm.select(below, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            return True
        except Exception:
            return False

    def _select_only_node_in_view(self, node) -> None:
        if self.tree is None:
            return
        sm = self.tree.selectionModel()
        if sm is None:
            return

        target = self._find_index_for_node(node)
        if target is None or not target.isValid():
            return
        try:
            sm.clearSelection()
            sm.setCurrentIndex(target, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        except Exception:
            pass

    def _find_index_for_node(self, node):
        if self.tree is None:
            return None
        model = self.tree.model()
        if model is None:
            return None

        node_uid = None
        try:
            if callable(getattr(node, "uniqueId", None)):
                node_uid = node.uniqueId()
        except Exception:
            node_uid = None

        def matches(idx) -> bool:
            for role in range(int(Qt.UserRole), int(Qt.UserRole) + 100):
                try:
                    v = idx.data(role)
                except Exception:
                    continue
                if v is None:
                    continue
                if v is node:
                    return True
                if callable(getattr(v, "uniqueId", None)) and node_uid is not None:
                    try:
                        if v.uniqueId() == node_uid:
                            return True
                    except Exception:
                        pass
                if callable(getattr(v, "name", None)) and callable(getattr(node, "name", None)):
                    try:
                        if v.name() == node.name():
                            if callable(getattr(v, "type", None)) and callable(getattr(node, "type", None)):
                                if v.type() == node.type():
                                    return True
                    except Exception:
                        pass
            return False

        # BFS traversal
        stack = [model.index(r, 0) for r in range(model.rowCount())]
        while stack:
            idx = stack.pop(0)
            if not idx.isValid():
                continue
            if matches(idx):
                return idx
            try:
                rc = model.rowCount(idx)
            except Exception:
                rc = 0
            if rc:
                for r in range(rc):
                    stack.append(model.index(r, 0, idx))
        return None

    @staticmethod
    def _index_of_widget(layout, widget: QWidget) -> int:
        """Find the index of a widget in a layout."""
        if layout is None or widget is None:
            return -1
        try:
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is None:
                    continue
                w = item.widget()
                if w is widget:
                    return i
        except Exception:
            pass
        return -1

    def _find_layer_tree_view(self) -> Optional[QTreeView]:
        """Find the layer tree view in the docker."""
        # First try by name - listLayers from WdgLayerBox.ui
        tree = self.docker.findChild(QTreeView, "listLayers")
        if tree is not None:
            return tree
            
        # Fallback to searching
        candidates = self.docker.findChildren(QTreeView)
        if not candidates:
            return None

        scored = []
        for tv in candidates:
            try:
                cn = (tv.metaObject().className() or "").lower()
            except Exception:
                cn = ""
            on = (tv.objectName() or "").lower()
            score = 0
            if "nodeview" in cn:
                score += 10
            if "kis" in cn and "tree" in cn:
                score += 5
            if "node" in cn or "layer" in cn:
                score += 4
            if "list" in on:
                score += 3
            try:
                if tv.selectionMode() == QAbstractItemView.ExtendedSelection:
                    score += 2
            except Exception:
                pass
            scored.append((score, tv))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored else None

    def _install_group_layer_labeling(self) -> None:
        """Install automatic color labeling for group layers using Krita's native system."""
        # Find tree for reference (used by other methods)
        self.tree = self._find_layer_tree_view()
        
        # Avoid double-initialization
        if self.docker.property("layer_kit_labeler") is True:
            return

        self._labeler = GroupLayerLabeler(self.docker)
        self._labeler.start_monitoring()
        self.docker.setProperty("layer_kit_labeler", True)


class LayerKitExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)

        self._patchers: Dict[int, LayersDockerPatcher] = {}
        self._retry_counts: Dict[int, int] = {}

        notifier = Krita.instance().notifier()
        notifier.windowCreated.connect(self._on_window_created)

        # Patch already-open windows with a delay
        QTimer.singleShot(500, self._apply_to_all_windows)

    def setup(self):
        pass

    def createActions(self, window):
        pass

    def _on_window_created(self, *args):
        QTimer.singleShot(500, self._apply_to_all_windows)

    def _apply_to_all_windows(self) -> None:
        try:
            windows = Krita.instance().windows()
        except Exception:
            windows = []
        for w in windows:
            self._apply_to_window(w)

    def _apply_to_window(self, window) -> None:
        try:
            qwin = window.qwindow()
        except Exception:
            qwin = None

        if qwin is None:
            return

        docker = qwin.findChild(QDockWidget, "KisLayerBox")
        if docker is None:
            self._schedule_retry(window)
            return

        if docker.property("layer_kit_patched") is True:
            return

        patcher = LayersDockerPatcher(docker, docker)
        try:
            patcher.apply()
            docker.setProperty("layer_kit_patched", True)
            self._patchers[int(id(docker))] = patcher
        except Exception:
            self._schedule_retry(window)

    def _schedule_retry(self, window) -> None:
        try:
            key = int(id(window))
        except Exception:
            return
        count = self._retry_counts.get(key, 0)
        if count >= 10:
            return
        self._retry_counts[key] = count + 1
        QTimer.singleShot(500, self._apply_to_all_windows)
