# Terceiros

O Loom é distribuído sob a [licença MIT](LICENSE). Este arquivo lista o que ele
inclui de terceiros, cada um sob a própria licença, que continua valendo.

## Versionado neste repositório

### xterm.js e addons
`ui/vendor/xterm.js`, `ui/vendor/xterm.css`, `ui/vendor/addon-fit.js`,
`ui/vendor/addon-search.js`, `ui/vendor/addon-webgl.js`, `ui/vendor/addon-weblinks.js`

Copyright (c) 2014 The xterm.js authors
Copyright (c) 2012-2013 Christopher Jeffrey

Licença MIT. https://github.com/xtermjs/xterm.js

### Qt WebChannel, lado JavaScript
`ui/vendor/qwebchannel.js`

Copyright (C) 2016 The Qt Company Ltd.
Copyright (C) 2016 Klarälvdalens Datakonsult AB, a KDAB Group company

`SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`

Este arquivo **não** é MIT. Ele vem do Qt e segue as licenças acima. Aqui ele é
usado sob a LGPL v3, sem modificação, exatamente como distribuído pelo Qt.
https://doc.qt.io/qt-6/qtwebchannel-index.html

### Fonte Hack
`ui/vendor/fonts/hack-regular.woff2`, `hack-bold.woff2`, `hack-italic.woff2`

Copyright (c) 2018 Source Foundry Authors, licença MIT.
Contém partes derivadas do Bitstream Vera Sans Mono, Copyright (c) 2003 Bitstream Inc.,
sob a Bitstream Vera License. https://github.com/source-foundry/Hack

### Fonte JetBrains Mono
`ui/vendor/fonts/jetbrains-mono-latin-400-normal.woff2`,
`jetbrains-mono-latin-700-normal.woff2`, `jetbrains-mono-latin-ext-400-normal.woff2`

Copyright (c) 2020 The JetBrains Mono Project Authors, sob a
SIL Open Font License 1.1. https://github.com/JetBrains/JetBrainsMono

## Empacotado no instalador

O `Loom-Setup.exe` é gerado com PyInstaller e leva junto:

- **Qt 6 e PySide6**, The Qt Company, sob LGPL v3. O Qt entra como biblioteca
  dinâmica, sem modificação, e pode ser substituído por outra build compatível.
  https://www.qt.io/licensing
- **pywinpty**, licença MIT, que embute o **winpty** de Ryan Prichard, também MIT.
  https://github.com/andfoy/pywinpty
- **Microsoft Visual C++ Redistributable**, redistribuível sob os termos da
  Microsoft. Ele é apenas baixado e executado pelo instalador, não é modificado.

## Como conferir

Os arquivos de terceiros ficam todos em `ui/vendor/`. Nenhum deles foi editado:
são cópias do que o projeto de origem publica.
