#include "backend.h"

#include <QGuiApplication>
#include <QQmlApplicationEngine>
#include <QQuickStyle>
#include <QQuickWindow>
#include <QTimer>

/*
 * Normal start:        QtPythonDemo.exe
 * Self test:           QtPythonDemo.exe --selftest
 *                      starts the back end, calls add(2, 3), exits 0 when the
 *                      answer 5 arrives (1 on error, 2 on timeout).  Use it to
 *                      check a bundle on another PC without clicking.
 * Picture of the UI:   QtPythonDemo.exe --screenshot out.png   (after 3 s)
 */
int main(int argc, char *argv[])
{
    QGuiApplication app(argc, argv);
    QGuiApplication::setApplicationName("QtPythonDemo");
    QGuiApplication::setOrganizationName("Guardinger");
    QQuickStyle::setStyle("Fusion");               // same look on every machine

    const QStringList args = QCoreApplication::arguments();

    if (args.contains("--selftest")) {
        Backend backend;
        QObject::connect(&backend, &Backend::stateChanged, &app, [&] {
            if (backend.state() == "ready")
                backend.call("add", { { "a", 2 }, { "b", 3 } });
            else if (backend.state() == "error")
                QCoreApplication::exit(1);
        });
        QObject::connect(&backend, &Backend::replied, &app, [&](int, const QString &, const QVariant &r) {
            QCoreApplication::exit(r.toDouble() == 5.0 ? 0 : 1);
        });
        QObject::connect(&backend, &Backend::failed, &app, [] { QCoreApplication::exit(1); });
        QTimer::singleShot(15000, &app, [] { QCoreApplication::exit(2); });
        backend.start();
        return app.exec();
    }

    QQmlApplicationEngine engine;
    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed, &app,
                     []() { QCoreApplication::exit(-1); }, Qt::QueuedConnection);
    engine.loadFromModule("QtPythonDemo", "Main");

    const int shot = args.indexOf("--screenshot");
    if (shot >= 0 && shot + 1 < args.size()) {
        const QString file = args.at(shot + 1);
        QTimer::singleShot(3000, &app, [&engine, file] {
            for (QObject *root : engine.rootObjects())
                if (auto *win = qobject_cast<QQuickWindow *>(root)) {
                    win->grabWindow().save(file);
                    break;
                }
            QCoreApplication::quit();
        });
    }
    return app.exec();
}
