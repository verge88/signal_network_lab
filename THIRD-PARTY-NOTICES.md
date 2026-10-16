# Third-party components

Signal Lab Desktop uses unmodified third-party libraries and dynamically loaded Qt DLLs. The portable directory keeps the libraries separate from the application executable. The application's own source is provided in this workspace.

| Component | Upstream / source | License information |
| --- | --- | --- |
| Qt / PySide6 Essentials / Shiboken6 | https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://code.qt.io/cgit/qt/qtbase.git/ | PySide wheel metadata: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. See https://doc.qt.io/qtforpython-6/ and https://doc.qt.io/qt-6/licensing.html. |
| Python | https://www.python.org/ | Python Software Foundation license; see https://docs.python.org/3/license.html. |
| NumPy | https://github.com/numpy/numpy | BSD-3-Clause; upstream distributions include additional dependency notices. |
| SciPy | https://github.com/scipy/scipy | BSD-3-Clause; upstream distributions include additional dependency notices. |
| scikit-learn | https://github.com/scikit-learn/scikit-learn | BSD-3-Clause. |
| joblib | https://github.com/joblib/joblib | BSD-3-Clause. |
| Pydantic / pydantic-core | https://github.com/pydantic/pydantic | MIT. |
| PyInstaller bootloader | https://github.com/pyinstaller/pyinstaller | GPL with bootloader exception; see https://pyinstaller.org/en/stable/license.html. |

Version information is recorded in requirements-native-verified.txt, runtime experiment metadata and packaged dependency metadata. Qt wheel-provided license files are retained under `_internal/licenses/Qt`. The wheel includes a commercial-license reference as an alternative; the project has not obtained or claimed a commercial Qt license. Upstream open-source license terms are available at the linked official sources. Segoe UI is loaded from the Windows installation and is not redistributed with this application.
