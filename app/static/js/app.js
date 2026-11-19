const dashboardAutoRefreshFlagKey = "gridMonitorDashboardAutoRefreshTriggered";
const dashboardAutoRefreshScrollKey = "gridMonitorDashboardAutoRefreshScrollY";

if (window.location.hash === "#alert-history" && !hasDashboardAutoRefreshFlag()) {
    document.documentElement.classList.add("no-smooth-scroll");
}

document.addEventListener("DOMContentLoaded", () => {
    const restoredAutoRefreshScroll = restoreDashboardScrollAfterRefresh();
    if (!restoredAutoRefreshScroll) {
        initAlertHistoryHashJump();
    }

    document.querySelectorAll("details.task-action-menu").forEach((menu) => {
        menu.addEventListener("toggle", () => {
            if (!menu.open) {
                resetTaskActionMenuPosition(menu);
                return;
            }

            document.querySelectorAll("details.task-action-menu[open]").forEach((otherMenu) => {
                if (otherMenu !== menu) {
                    otherMenu.open = false;
                }
            });

            positionTaskActionMenu(menu);
        });
    });

    document.addEventListener("click", (event) => {
        if (event.target.closest("details.task-action-menu")) {
            return;
        }

        document.querySelectorAll("details.task-action-menu[open]").forEach((menu) => {
            menu.open = false;
        });
    });

    window.addEventListener("resize", () => {
        document.querySelectorAll("details.task-action-menu[open]").forEach(positionTaskActionMenu);
    });

    window.addEventListener("scroll", () => {
        document.querySelectorAll("details.task-action-menu[open]").forEach(positionTaskActionMenu);
    }, true);

    document.querySelectorAll(".alert-preview-open-btn").forEach((button) => {
        button.addEventListener("click", () => {
            const dialogId = button.dataset.dialogTarget;
            const dialog = document.getElementById(dialogId);
            if (!dialog) {
                return;
            }

            if (typeof dialog.showModal === "function") {
                dialog.showModal();
            } else {
                dialog.setAttribute("open", "");
            }
        });
    });

    document.querySelectorAll(".alert-detail-toggle-btn").forEach((button) => {
        button.addEventListener("click", () => {
            const detailsRow = document.getElementById(button.dataset.alertDetailsTarget);
            if (!detailsRow) {
                return;
            }
            const willOpen = detailsRow.hidden;
            detailsRow.hidden = !willOpen;
            button.setAttribute("aria-expanded", String(willOpen));
            button.textContent = willOpen ? "收起详情" : "查看详情";
        });
    });

    document.querySelectorAll(".notification-preview-dialog").forEach((dialog) => {
        dialog.querySelectorAll(".notification-preview-close-btn").forEach((button) => {
            button.addEventListener("click", () => {
                dialog.close();
            });
        });

        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) {
                dialog.close();
            }
        });
    });

    document.querySelectorAll(".copy-notification-btn").forEach((button) => {
        button.addEventListener("click", async () => {
            const targetId = button.dataset.copyTarget;
            const target = document.getElementById(targetId);
            if (!target) {
                return;
            }

            const originalText = button.textContent;
            const text = target.textContent;
            const copied = await copyText(text);
            button.textContent = copied ? "已复制" : "复制失败";
            button.disabled = true;

            window.setTimeout(() => {
                button.textContent = originalText;
                button.disabled = false;
            }, 1600);
        });
    });

    initDashboardClock();
    initDashboardRefreshControls();
});

function initAlertHistoryHashJump() {
    if (hasDashboardAutoRefreshFlag()) {
        document.documentElement.classList.remove("no-smooth-scroll");
        return;
    }

    if (window.location.hash !== "#alert-history") {
        document.documentElement.classList.remove("no-smooth-scroll");
        return;
    }

    const target = document.getElementById("alert-history");
    if (!target) {
        document.documentElement.classList.remove("no-smooth-scroll");
        return;
    }

    window.setTimeout(() => {
        const offset = 14;
        const top = target.getBoundingClientRect().top + window.scrollY - offset;
        window.scrollTo({ top: Math.max(0, top), behavior: "auto" });
        window.setTimeout(() => {
            document.documentElement.classList.remove("no-smooth-scroll");
        }, 80);
    }, 0);
}

function positionTaskActionMenu(menu) {
    const summary = menu.querySelector("summary");
    const content = menu.querySelector(".task-action-menu-content");
    if (!summary || !content) {
        return;
    }

    const gap = 6;
    const viewportPadding = 10;
    const summaryRect = summary.getBoundingClientRect();
    const contentRect = content.getBoundingClientRect();
    const contentWidth = contentRect.width || 172;
    const contentHeight = contentRect.height || 120;
    const spaceBelow = window.innerHeight - summaryRect.bottom - viewportPadding;
    const spaceAbove = summaryRect.top - viewportPadding;
    const shouldDropUp = spaceBelow < contentHeight + gap && spaceAbove > spaceBelow;

    const left = Math.min(
        Math.max(viewportPadding, summaryRect.right - contentWidth),
        window.innerWidth - contentWidth - viewportPadding,
    );
    const top = shouldDropUp
        ? Math.max(viewportPadding, summaryRect.top - contentHeight - gap)
        : Math.min(summaryRect.bottom + gap, window.innerHeight - contentHeight - viewportPadding);

    menu.classList.toggle("is-dropup", shouldDropUp);
    content.style.left = `${left}px`;
    content.style.top = `${top}px`;
}

function resetTaskActionMenuPosition(menu) {
    const content = menu.querySelector(".task-action-menu-content");
    menu.classList.remove("is-dropup");
    if (!content) {
        return;
    }

    content.style.left = "";
    content.style.top = "";
}

function initDashboardClock() {
    const clock = document.getElementById("dashboardBeijingClock");
    if (!clock) {
        return;
    }

    const updateClock = () => {
        clock.textContent = formatBeijingDateTime(new Date());
    };

    updateClock();
    window.setInterval(updateClock, 1000);
}

function initDashboardRefreshControls() {
    const controls = document.querySelector(".dashboard-refresh-controls");
    if (!controls) {
        return;
    }

    const lastRefresh = document.getElementById("dashboardLastRefresh");
    const refreshButton = document.getElementById("dashboardRefreshNow");
    const autoRefreshToggle = document.getElementById("dashboardAutoRefreshToggle");
    const countdown = document.getElementById("dashboardAutoRefreshCountdown");
    const intervalSeconds = Number.parseInt(controls.dataset.autoRefreshInterval || "60", 10);
    const refreshInterval = Number.isFinite(intervalSeconds) && intervalSeconds > 0 ? intervalSeconds : 60;
    let secondsRemaining = refreshInterval;
    const storageKey = "gridMonitorDashboardAutoRefresh";

    if (lastRefresh) {
        lastRefresh.textContent = formatLocalDateTime(new Date());
    }

    if (autoRefreshToggle) {
        autoRefreshToggle.checked = window.localStorage.getItem(storageKey) === "true";
        autoRefreshToggle.addEventListener("change", () => {
            window.localStorage.setItem(storageKey, autoRefreshToggle.checked ? "true" : "false");
            secondsRemaining = refreshInterval;
            updateRefreshCountdown(countdown, autoRefreshToggle.checked, secondsRemaining);
        });
    }

    if (refreshButton) {
        refreshButton.addEventListener("click", () => {
            refreshDashboardWithoutHash(true);
        });
    }

    updateRefreshCountdown(countdown, Boolean(autoRefreshToggle && autoRefreshToggle.checked), secondsRemaining);

    window.setInterval(() => {
        if (!autoRefreshToggle || !autoRefreshToggle.checked) {
            updateRefreshCountdown(countdown, false, secondsRemaining);
            return;
        }

        if (isDashboardRefreshPaused()) {
            updateRefreshCountdown(countdown, true, secondsRemaining, true);
            return;
        }

        secondsRemaining -= 1;
        if (secondsRemaining <= 0) {
            refreshDashboardWithoutHash(true);
            return;
        }

        updateRefreshCountdown(countdown, true, secondsRemaining);
    }, 1000);
}

function refreshDashboardWithoutHash(preserveScroll = false) {
    if (preserveScroll) {
        try {
            window.sessionStorage.setItem(dashboardAutoRefreshFlagKey, "1");
            window.sessionStorage.setItem(dashboardAutoRefreshScrollKey, String(window.scrollY || 0));
        } catch (error) {
            // sessionStorage may be unavailable in hardened browser modes.
        }
    }

    const refreshUrl = `${window.location.pathname}${window.location.search}`;
    window.location.replace(refreshUrl || "/");
}

function restoreDashboardScrollAfterRefresh() {
    if (!hasDashboardAutoRefreshFlag()) {
        return false;
    }

    let scrollY = 0;
    try {
        scrollY = Number.parseInt(window.sessionStorage.getItem(dashboardAutoRefreshScrollKey) || "0", 10);
        window.sessionStorage.removeItem(dashboardAutoRefreshFlagKey);
        window.sessionStorage.removeItem(dashboardAutoRefreshScrollKey);
    } catch (error) {
        scrollY = 0;
    }

    window.setTimeout(() => {
        window.scrollTo({ top: Number.isFinite(scrollY) ? Math.max(0, scrollY) : 0, behavior: "auto" });
        document.documentElement.classList.remove("no-smooth-scroll");
    }, 0);
    return true;
}

function hasDashboardAutoRefreshFlag() {
    try {
        return window.sessionStorage.getItem(dashboardAutoRefreshFlagKey) === "1";
    } catch (error) {
        return false;
    }
}

function isDashboardRefreshPaused() {
    return (
        document.hidden ||
        Boolean(document.querySelector(".notification-preview-dialog[open]")) ||
        Boolean(document.querySelector("details.task-action-menu[open]"))
    );
}

function updateRefreshCountdown(countdown, enabled, secondsRemaining, paused = false) {
    if (!countdown) {
        return;
    }

    if (!enabled) {
        countdown.textContent = "下次刷新：未开启";
        return;
    }

    if (paused) {
        countdown.textContent = "下次刷新：已暂停";
        return;
    }

    countdown.textContent = `下次刷新：${secondsRemaining} 秒后`;
}

function formatLocalDateTime(value) {
    const pad = (number) => String(number).padStart(2, "0");
    return [
        value.getFullYear(),
        pad(value.getMonth() + 1),
        pad(value.getDate()),
    ].join("-") + " " + [
        pad(value.getHours()),
        pad(value.getMinutes()),
        pad(value.getSeconds()),
    ].join(":");
}

function formatBeijingDateTime(value) {
    const parts = new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    }).formatToParts(value).reduce((result, part) => {
        if (part.type !== "literal") {
            result[part.type] = part.value;
        }
        return result;
    }, {});

    return `${parts.year}/${parts.month}/${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (error) {
            return fallbackCopyText(text);
        }
    }

    return fallbackCopyText(text);
}

function fallbackCopyText(text) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();

    try {
        return document.execCommand("copy");
    } catch (error) {
        return false;
    } finally {
        document.body.removeChild(textarea);
    }
}
