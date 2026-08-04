"""Validate ODB files and upgrade older databases with the local Abaqus release."""

from __future__ import print_function

import argparse
import json
import os
import sys

from odbAccess import isUpgradeRequiredForOdb, openOdb, upgradeOdb


def emit(message):
    print(message)
    sys.stdout.flush()


def write_report(path, payload):
    with open(path, "w") as stream:
        # Keep the interchange file ASCII-only. Abaqus inherits the Windows
        # locale (often GBK), while the host GUI uses UTF-8.
        json.dump(payload, stream, ensure_ascii=True, indent=2)


def classify_error(message):
    lowered = message.lower()
    if "previous release" in lowered or "earlier release" in lowered:
        return "upgrade_required"
    if "newer release" in lowered or "installation must be upgraded" in lowered:
        return "newer_release"
    return "invalid"


def check_one(path):
    result = {"path": path}
    if not os.path.isfile(path):
        result.update(status="missing", message="文件不存在")
        return result
    size_bytes = os.path.getsize(path)
    result["size_bytes"] = size_bytes
    if size_bytes <= 0:
        result.update(status="empty", message="文件大小为 0")
        return result
    try:
        if isUpgradeRequiredForOdb(upgradeRequiredOdbPath=path):
            result.update(
                status="upgrade_required",
                message="旧版 ODB，需要升级到本机 Abaqus 版本",
            )
            return result
        odb = openOdb(path=path, readOnly=True)
        try:
            result["step_count"] = len(odb.steps)
        finally:
            odb.close()
        result.update(status="valid", message="可由本机 Abaqus 直接读取")
        return result
    except Exception as error:
        message = str(error)
        result.update(status=classify_error(message), message=message)
        return result


def check_mode(request, output_path):
    paths = request.get("paths", [])
    results = []
    progress_start = int(request.get("progress_start", 0))
    total = int(request.get("progress_total", len(paths)))
    for relative_index, path in enumerate(paths, 1):
        index = progress_start + relative_index
        result = check_one(path)
        results.append(result)
        emit(
            "ODB_CHECK|%d|%d|%s|%s"
            % (index, total, result.get("status", "invalid"), os.path.basename(path))
        )
        write_report(output_path, {"mode": "check", "results": results})
    return {"mode": "check", "results": results}


def upgrade_one(source_path, upgraded_path, backup_path, temporary_path):
    result = {
        "source_path": source_path,
        "upgraded_path": upgraded_path,
        "backup_path": backup_path,
    }
    if not os.path.isfile(source_path):
        result.update(status="missing", message="源文件不存在")
        return result
    if os.path.abspath(upgraded_path) != os.path.abspath(source_path):
        result.update(status="invalid", message="新版 ODB 必须保持源文件名")
        return result
    if os.path.exists(backup_path):
        result.update(status="target_exists", message="旧版 ODB 备份已存在，拒绝覆盖")
        return result
    if os.path.exists(temporary_path):
        result.update(status="target_exists", message="升级临时文件已存在，拒绝覆盖")
        return result
    destination_dir = os.path.dirname(source_path)
    if destination_dir and not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)
    previous_cwd = os.getcwd()
    backup_created = False
    try:
        if destination_dir:
            os.chdir(destination_dir)
        upgradeOdb(
            existingOdbPath=source_path,
            upgradedOdbPath=temporary_path,
        )
        checked = check_one(temporary_path)
        if checked.get("status") != "valid":
            raise RuntimeError(
                "升级文件生成后仍无法读取：%s" % checked.get("message", "未知错误")
            )
        os.rename(source_path, backup_path)
        backup_created = True
        os.rename(temporary_path, upgraded_path)
        final_checked = check_one(upgraded_path)
        if final_checked.get("status") != "valid":
            raise RuntimeError(
                "新版 ODB 替换后验证失败：%s"
                % final_checked.get("message", "未知错误")
            )
        result.update(
            status="upgraded",
            message="新版保持原名；旧版已增加 -old 后缀并保留",
            size_bytes=final_checked.get("size_bytes", 0),
            step_count=final_checked.get("step_count", 0),
        )
        return result
    except Exception as error:
        rollback_errors = []
        if backup_created:
            try:
                if os.path.exists(upgraded_path):
                    os.remove(upgraded_path)
                os.rename(backup_path, source_path)
                backup_created = False
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except Exception as cleanup_error:
                rollback_errors.append(str(cleanup_error))
        message = str(error)
        if rollback_errors:
            message += "；自动恢复/清理失败：" + " | ".join(rollback_errors)
        else:
            message += "；原 ODB 已保持或恢复"
        result.update(status="upgrade_failed", message=message)
        return result
    finally:
        os.chdir(previous_cwd)


def upgrade_mode(request, output_path):
    tasks = request.get("tasks", [])
    results = []
    total = len(tasks)
    for index, task in enumerate(tasks, 1):
        source_path = task["source_path"]
        upgraded_path = task["upgraded_path"]
        backup_path = task["backup_path"]
        temporary_path = task["temporary_path"]
        emit(
            "ODB_UPGRADE_START|%d|%d|%s"
            % (index, total, os.path.basename(source_path))
        )
        result = upgrade_one(
            source_path,
            upgraded_path,
            backup_path,
            temporary_path,
        )
        results.append(result)
        emit(
            "ODB_UPGRADE_DONE|%d|%d|%s|%s"
            % (
                index,
                total,
                result.get("status", "upgrade_failed"),
                os.path.basename(source_path),
            )
        )
        write_report(output_path, {"mode": "upgrade", "results": results})
    return {"mode": "upgrade", "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check", "upgrade"), required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    with open(arguments.request, "r") as stream:
        request = json.load(stream)
    if arguments.mode == "check":
        payload = check_mode(request, arguments.output)
    else:
        payload = upgrade_mode(request, arguments.output)
    write_report(arguments.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
