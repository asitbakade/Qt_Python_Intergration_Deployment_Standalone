cmake_minimum_required(VERSION 3.21)

# QtPythonDemo - a minimal Qt (C++ / QML) front end with a Python back end,
# packaged into one standalone folder by tools/package.py.
project(QtPythonDemo VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2)
qt_standard_project_setup(REQUIRES 6.5)
if(QT_KNOWN_POLICY_QTP0004)
    qt_policy(SET QTP0004 NEW)      # QML files may live in a sub folder (frontend/qml)
endif()

qt_add_executable(QtPythonDemo
    frontend/main.cpp
)

# The QML UI plus the C++ types it uses.  `Backend` (backend.h) carries
# QML_ELEMENT, so it must be listed here (SOURCES of the QML module) for Qt to
# generate its QML registration.
qt_add_qml_module(QtPythonDemo
    URI QtPythonDemo
    VERSION 1.0
    QML_FILES
        frontend/qml/Main.qml
    SOURCES
        frontend/backend.h
        frontend/backend.cpp
)

# In development the app runs backend/backend.py straight from the source
# tree with the Python on PATH (see Backend::locate).  In the packaged bundle
# it finds backend/ next to the .exe instead.
target_compile_definitions(QtPythonDemo PRIVATE DEMO_SOURCE_DIR="${CMAKE_SOURCE_DIR}")
target_include_directories(QtPythonDemo PRIVATE frontend)   # the generated QML registration includes <backend.h>

target_link_libraries(QtPythonDemo PRIVATE
    Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2
)

set_target_properties(QtPythonDemo PROPERTIES
    WIN32_EXECUTABLE TRUE            # GUI app: no console window
    MACOSX_BUNDLE TRUE
)
