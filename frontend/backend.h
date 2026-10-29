#ifndef BACKEND_H
#define BACKEND_H

#include <QHash>
#include <QObject>
#include <QProcess>
#include <QString>
#include <QVariant>
#include <QVariantMap>
#include <QtQml/qqmlregistration.h>

/*
 * Backend - runs the Python back end as a child process and talks JSON to it
 * ---------------------------------------------------------------------------
 * One request per line on the child's stdin, one response per line on its
 * stdout (see backend/backend.py).  Everything is asynchronous: call() returns
 * a request id at once, the answer arrives later as replied() or failed().
 *
 *   Backend { id: backend; Component.onCompleted: start() }
 *   backend.call("add", { a: 2, b: 3 })
 *   onReplied: (id, method, result) => ...
 *
 * Where the Python comes from (locate()):
 *   1. <app dir>/backend/python/python.exe  <app dir>/backend/backend.py   - the packaged bundle
 *   2. python  <app dir>/backend/backend.py                                - bundle without its own Python
 *   3. python  <source dir>/backend/backend.py                             - development (Qt Creator)
 */
class Backend : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(QString state READ state NOTIFY stateChanged)       // starting | ready | stopped | error
    Q_PROPERTY(QString command READ command NOTIFY stateChanged)   // what was launched, for display
    Q_PROPERTY(QString lastError READ lastError NOTIFY stateChanged)

public:
    explicit Backend(QObject *parent = nullptr);
    ~Backend() override;

    QString state() const { return m_state; }
    QString command() const { return m_command; }
    QString lastError() const { return m_lastError; }

    Q_INVOKABLE void start();
    Q_INVOKABLE void stop();
    Q_INVOKABLE int call(const QString &method, const QVariantMap &params = {});

signals:
    void stateChanged();
    void replied(int id, const QString &method, const QVariant &result);
    void failed(int id, const QString &method, const QString &error);
    void logLine(const QString &line);          // raw traffic and stderr, for the demo's log pane

private:
    void onStdout();
    void onStderr();
    void onFinished(int code, QProcess::ExitStatus status);
    void onError(QProcess::ProcessError error);
    void handleLine(const QByteArray &line);
    void setState(const QString &s, const QString &error = QString());
    static QStringList locate();               // program + arguments, empty if nothing found

    QProcess m_proc;
    QByteArray m_buffer;
    QHash<int, QString> m_pending;             // request id -> method
    int m_nextId = 1;
    QString m_state = QStringLiteral("stopped");
    QString m_command;
    QString m_lastError;
};

#endif // BACKEND_H
