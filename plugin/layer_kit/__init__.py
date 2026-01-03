# Layer Kit - Krita Python Plugin
# SPDX-License-Identifier: MIT
from krita import Krita

from .layer_kit import LayerKitExtension

Krita.instance().addExtension(LayerKitExtension(Krita.instance()))
