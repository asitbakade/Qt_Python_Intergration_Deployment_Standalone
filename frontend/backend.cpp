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
    const QDir appDir(QCoreApplication::applicationDirPath());

    // 1. packaged bundle: our own embeddable Python next to the exe
    const QString bundledPy = appDir.filePath("backend/python/python.exe");
    const QString bundledScript = appDir.filePath("backend/backend.py");
    if (QFileInfo::exists(bundledPy) && QFileInfo::exists(bundledScript))
        return { bundledPy, "-u", bundledScript };

    // 2. backend folder next to the exe, Python from PATH
    const QString python = QStandardPaths::findExecutable("python");
    if (!python.isEmpty() && QFileInfo::exists(bundledScript))
        return { python, "-u", bundledScript };

    // 3. development: the source tree (path baked in by CMake)
    const QString devScript = QDir(QString::fromUtf8(DEMO_SOURCE_DIR)).filePath("backend/backend.py");
    if (!python.isEmpty() && QFileInfo::exists(devScript))
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
        setState("error", "no Python back end found (backend/python/python.exe or python on PATH)");
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
