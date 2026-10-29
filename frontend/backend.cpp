#include "backend.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcessEnvironment>
#include <QStandardPaths>

#ifndef DEMO_SOURCE_DIR
#define DEMO_SOURCE_DIR ""
#endif

Backend::Backend(QObject *parent)
    : QObject(parent)
{
    connect(&m_proc, &QProcess::readyReadStandardOutput, this, &Backend::onStdout);
    connect(&m_proc, &QProcess::readyReadStandardError, this, &Backend::onStderr);
    connect(&m_proc, &QProcess::finished, this, &Backend::onFinished);
    connect(&m_proc, &QProcess::errorOccurred, this, &Backend::onError);
}

Backend::~Backend()
{
    stop();
}

// ---------------------------------------------------------------- where is Python?

QStringList Backend::locate()
{
    // The bundle layout differs per platform (see tools/package.py):
    //   Windows:  <bundle>/QtPythonDemo.exe            backend/ is next to the exe
    //   Linux:    <bundle>/bin/QtPythonDemo            backend/ is one level up
    //   macOS:    <bundle>.app/Contents/MacOS/...      backend/ in Contents/Resources
    const QString appDir = QCoreApplication::applicationDirPath();
    const QStringList bases = { appDir, appDir + "/..", appDir + "/../Resources" };
#ifdef Q_OS_WIN
    const QString privatePython = "backend/python/python.exe";
    const QStringList pathNames = { "python", "python3" };
#else
    const QString privatePython = "backend/python/bin/python3";
    const QStringList pathNames = { "python3", "python" };
#endif

    // 1. packaged bundle: our own private Python + our script
    for (const QString &base : bases) {
        const QDir d(base);
        const QString py = d.filePath(privatePython);
        const QString script = d.filePath("backend/backend.py");
        if (QFileInfo::exists(py) && QFileInfo::exists(script))
            return { QDir::cleanPath(py), "-u", QDir::cleanPath(script) };
    }

    QString python;
    for (const QString &name : pathNames) {
        python = QStandardPaths::findExecutable(name);
        if (!python.isEmpty())
            break;
    }
    if (python.isEmpty())
        return {};

    // 2. backend folder in the bundle, Python from PATH
    for (const QString &base : bases) {
        const QString script = QDir(base).filePath("backend/backend.py");
        if (QFileInfo::exists(script))
            return { python, "-u", QDir::cleanPath(script) };
    }

    // 3. development: the source tree (path baked in by CMake)
    const QString devScript = QDir(QString::fromUtf8(DEMO_SOURCE_DIR)).filePath("backend/backend.py");
    if (QFileInfo::exists(devScript))
        return { python, "-u", devScript };

    return {};
}

// ---------------------------------------------------------------- process control

void Backend::start()
{
    if (m_proc.state() != QProcess::NotRunning)
        return;
    const QStringList cmd = locate();
    if (cmd.isEmpty()) {
        setState("error", "no Python back end found (backend/python/ in the bundle, or python3 on PATH)");
        return;
    }
    m_command = cmd.join(' ');
    m_buffer.clear();
    m_pending.clear();

    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert("PYTHONUNBUFFERED", "1");      // answers must not wait in a buffer
    env.insert("PYTHONIOENCODING", "utf-8");
    env.insert("PYTHONDONTWRITEBYTECODE", "1");
    m_proc.setProcessEnvironment(env);
    m_proc.setWorkingDirectory(QFileInfo(cmd.last()).absolutePath());
    emit logLine("$ " + m_command);
    setState("starting");
    m_proc.start(cmd.first(), cmd.mid(1));
}

void Backend::stop()
{
    if (m_proc.state() == QProcess::NotRunning)
        return;
    m_proc.closeWriteChannel();               // EOF on stdin: backend.py returns from its loop
    if (!m_proc.waitForFinished(2000))
        m_proc.kill();
}

int Backend::call(const QString &method, const QVariantMap &params)
{
    const int id = m_nextId++;
    if (m_proc.state() != QProcess::Running) {
        emit failed(id, method, "back end is not running");
        return id;
    }
    QJsonObject req;
    req["id"] = id;
    req["method"] = method;
    req["params"] = QJsonObject::fromVariantMap(params);
    const QByteArray line = QJsonDocument(req).toJson(QJsonDocument::Compact) + '\n';
    m_pending.insert(id, method);
    m_proc.write(line);
    emit logLine("-> " + QString::fromUtf8(line).trimmed());
    return id;
}

// ---------------------------------------------------------------- answers

void Backend::onStdout()
{
    m_buffer += m_proc.readAllStandardOutput();
    int nl;
    while ((nl = m_buffer.indexOf('\n')) >= 0) {
        const QByteArray line = m_buffer.left(nl).trimmed();
        m_buffer.remove(0, nl + 1);
        if (!line.isEmpty())
            handleLine(line);
    }
}

void Backend::handleLine(const QByteArray &line)
{
    emit logLine("<- " + QString::fromUtf8(line));
    QJsonParseError err;
    const QJsonDocument doc = QJsonDocument::fromJson(line, &err);
    if (!doc.isObject()) {
        emit logLine("   (not JSON: " + err.errorString() + ")");
        return;
    }
    const QJsonObject obj = doc.object();
    if (obj.value("event").toString() == "ready") {
        setState("ready");
        return;
    }
    const int id = obj.value("id").toInt(-1);
    const QString method = m_pending.take(id);
    if (obj.contains("error"))
        emit failed(id, method, obj.value("error").toString());
    else
        emit replied(id, method, obj.value("result").toVariant());
}

void Backend::onStderr()
{
    const QString text = QString::fromUtf8(m_proc.readAllStandardError());
    for (const QString &l : text.split('\n', Qt::SkipEmptyParts))
        emit logLine("py: " + l.trimmed());
}

void Backend::onFinished(int code, QProcess::ExitStatus status)
{
    for (auto it = m_pending.cbegin(); it != m_pending.cend(); ++it)
        emit failed(it.key(), it.value(), "back end exited");
    m_pending.clear();
    if (status == QProcess::CrashExit || code != 0)
        setState("error", QStringLiteral("back end exited with code %1").arg(code));
    else
        setState("stopped");
}

void Backend::onError(QProcess::ProcessError error)
{
    if (error == QProcess::FailedToStart)
        setState("error", "failed to start: " + m_proc.errorString());
}

void Backend::setState(const QString &s, const QString &error)
{
    m_state = s;
    m_lastError = error;
    if (!error.isEmpty())
        emit logLine("!! " + error);
    emit stateChanged();
}
