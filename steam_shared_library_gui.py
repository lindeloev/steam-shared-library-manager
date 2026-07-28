#!/usr/bin/env python3
"""Small graphical guide for a shared Steam library on Ubuntu.

The interface can create the filesystem library, configure Linux accounts, and
register default Steam storage while the client is closed. It then shows the
remaining compatibility-tool choice each Steam account must perform itself.
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from manager_model import build_game_rows, format_admin_request


HERE = Path(__file__).resolve().parent
GROUP = "steamgames"
PKEXEC = "/usr/bin/pkexec"
SYSTEM_PYTHON = "/usr/bin/python3"
STATE_VERSION = 1


class SharedSteamGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Steam Shared Library Manager")
        self.minsize(900, 720)
        self.geometry("1050x800")
        self.library = tk.StringVar(value=self.default_library())
        self.make_library_default = tk.BooleanVar(value=True)
        self.status: dict[str, object] = {}
        self.user_names: list[str] = []
        self.selected_names: set[str] = set()
        self.step_cards: list[dict[str, object]] = []
        self.failed_steps: set[int] = set()
        self.status_checked = False
        self.active_step = 1
        self.load_state()
        self.admin_process: subprocess.Popen[str] | None = None
        self.admin_lock = threading.Lock()
        self.active_tasks = 0
        self.build_window()
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        self.populate_user_choices()
        self.update_journey()

    @staticmethod
    def default_library() -> str:
        # Prefer a library Steam already knows for the person launching the
        # guide. No project-specific setting is needed or written to disk.
        config = Path.home() / ".steam/root/config/libraryfolders.vdf"
        try:
            paths = re.findall(r'"path"\s*"([^"]+)"', config.read_text(errors="replace"))
        except OSError:
            paths = []
        for path in paths:
            candidate = Path(path.replace("\\\\", "/"))
            if "/.steam/" not in str(candidate) and (candidate / "steamapps").is_dir():
                return str(candidate)
        return "/srv/SteamLibrary"

    @staticmethod
    def state_path() -> Path:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        return config_home / "steam-shared-library-manager" / "state.json"

    @staticmethod
    def running_in_bubblewrap() -> bool:
        """Detect the Codex/Bubblewrap case that cannot see host group IDs."""
        try:
            return b"bwrap" in Path("/proc/1/cmdline").read_bytes()
        except OSError:
            return False

    @classmethod
    def diagnostic_log_path(cls) -> Path:
        state_home = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        return state_home / "steam-shared-library-manager" / "manager.log"

    def append_diagnostic(self, message: str) -> None:
        """Keep a private, persistent record of actions and status results."""
        path = self.diagnostic_log_path()
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                timestamp = datetime.now().isoformat(timespec="seconds")
                handle.write(f"{timestamp} {message.rstrip()}\n")
            path.chmod(0o600)
        except OSError:
            pass

    def load_state(self) -> None:
        """Restore only harmless navigation choices; live state is always rescanned."""
        try:
            data = json.loads(self.state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return
        library = data.get("library")
        if isinstance(library, str) and library.startswith("/") and library not in ("/", "/usr") and not library.startswith("/usr/"):
            self.library.set(library)
        people = data.get("selected_users")
        if isinstance(people, list) and all(isinstance(name, str) for name in people):
            self.selected_names = set(people)
        step = data.get("last_step")
        if isinstance(step, int) and 1 <= step <= 6:
            self.active_step = step

    def save_state(self) -> None:
        """Atomically save resume choices without saving credentials or status."""
        path = self.state_path()
        data = {
            "version": STATE_VERSION,
            "library": self.library.get(),
            "selected_users": self.selected_users(),
            "last_step": self.active_step,
        }
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
        except OSError:
            # Resume state is a convenience and must never block the setup.
            pass

    def select_step(self, step: int) -> None:
        self.active_step = step
        self.save_state()
        self.show_step(step)

    def build_window(self) -> None:
        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")
        ttk.Label(header, text="🎮  Steam Shared Library Manager", font=("TkDefaultFont", 16, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="One shared game installation; personal Windows-game settings for each person.").grid(row=1, column=0, sticky="w", pady=(2, 8))
        ttk.Button(header, text="Refresh full status", command=self.refresh_status).grid(row=2, column=0, sticky="w")
        self.status_label = ttk.Label(header, text="Not checked since launch — Refresh full status", foreground="#991b1b")
        self.status_label.grid(row=2, column=1, sticky="w", padx=8)
        self.activity = ttk.Frame(header)
        self.activity_label = ttk.Label(self.activity)
        self.activity_label.pack(side="left")
        self.activity_bar = ttk.Progressbar(self.activity, mode="indeterminate", length=140)
        self.activity_bar.pack(side="left", padx=(10, 0))
        self.activity.grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.activity.grid_remove()
        self.steam_client_warning = ttk.Label(header, foreground="#991b1b", wraplength=780)
        self.steam_client_warning.grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 0))
        header.columnconfigure(1, weight=1)

        self.setup_tab = ttk.Frame(self, padding=12)
        self.setup_tab.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.build_setup_tab()

        log_panel = ttk.LabelFrame(self, text="Activity and command log", padding=(8, 6))
        log_panel.pack(fill="x", padx=12, pady=(0, 12))
        log_toolbar = ttk.Frame(log_panel)
        log_toolbar.pack(fill="x", pady=(0, 5))
        ttk.Label(
            log_toolbar,
            text="Shows every requested command, its exit status, and action or error output.",
            foreground="#555555",
        ).pack(side="left")
        ttk.Button(log_toolbar, text="Copy", command=self.copy_log).pack(side="right")
        ttk.Button(log_toolbar, text="Clear", command=self.clear_log).pack(side="right", padx=(0, 6))
        log_view = ttk.Frame(log_panel)
        log_view.pack(fill="x")
        log_scrollbar = ttk.Scrollbar(log_view, orient="vertical")
        log_scrollbar.pack(side="right", fill="y")
        self.log = tk.Text(
            log_view,
            height=6,
            wrap="word",
            state="disabled",
            font="TkFixedFont",
            background="#111827",
            foreground="#e5e7eb",
            insertbackground="#e5e7eb",
            padx=7,
            pady=6,
            yscrollcommand=log_scrollbar.set,
        )
        log_scrollbar.configure(command=self.log.yview)
        self.log.tag_configure("command", foreground="#93c5fd")
        self.log.tag_configure("success", foreground="#86efac")
        self.log.tag_configure("error", foreground="#fca5a5")
        self.log.tag_configure("muted", foreground="#9ca3af")
        self.log.pack(side="left", fill="x", expand=True)
        if self.running_in_bubblewrap():
            warning = ("This manager is running inside a Bubblewrap sandbox. Its permission checks may not match Steam on the host. "
                       "Close it and launch ./launch-gui.sh from a normal host terminal before changing or diagnosing shared-library access.")
            ttk.Label(header, text=warning, foreground="#991b1b", wraplength=780).grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))
            self.append_diagnostic("WARNING: Bubblewrap sandbox detected; host permission checks are unreliable")
        self.append_diagnostic("Manager started: user=" + self.current_user_name()
                               + " effective_groups=" + ",".join(map(str, os.getgroups()))
                               + " configured_steamgames=" + str(self.current_user_configured_in_group()))
        self.log_message("Persistent diagnostic log: " + str(self.diagnostic_log_path()), "muted")

    def build_setup_tab(self) -> None:
        ttk.Label(self.setup_tab, text="Setup journey", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(self.setup_tab, text="Complete each step until its marker turns green. Select a step to work on it.", foreground="#555555").pack(anchor="w", pady=(2, 8))
        journey = ttk.Frame(self.setup_tab)
        journey.pack(fill="both", expand=True)
        navigation = tk.Frame(
            journey,
            width=260,
            height=285,
            background="#f3f4f6",
            padx=7,
            pady=5,
        )
        self.journey_navigation = navigation
        navigation.pack(side="left", fill="y", padx=(0, 14))
        navigation.pack_propagate(False)
        detail_area = ttk.Frame(journey)
        detail_area.pack(side="left", fill="both", expand=True)
        self.detail_canvas = tk.Canvas(detail_area, highlightthickness=0, borderwidth=0)
        detail_scrollbar = ttk.Scrollbar(detail_area, orient="vertical", command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=detail_scrollbar.set)
        detail_scrollbar.pack(side="right", fill="y")
        self.detail_canvas.pack(side="left", fill="both", expand=True)
        self.step_detail = ttk.Frame(self.detail_canvas, padding=(2, 2, 12, 2))
        self.detail_window = self.detail_canvas.create_window((0, 0), window=self.step_detail, anchor="nw")
        self.step_detail.bind("<Configure>", lambda _event: self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all")))
        self.detail_canvas.bind("<Configure>", lambda event: self.detail_canvas.itemconfigure(self.detail_window, width=event.width))
        titles = ["👥  Choose people", "📁  Shared game folder", "↪  Restart session",
                  "⬇  Add folder and Proton", "⚙  Set up people", "🏁  Finish and check games"]
        for number, title in enumerate(titles, start=1):
            card = tk.Frame(navigation, highlightthickness=1, highlightbackground="#d1d5db", padx=7, pady=4)
            card.pack(fill="x", pady=2)
            title_button = tk.Button(card, text=f"○  {number}.  {title}", anchor="w", relief="flat",
                                     font=("TkDefaultFont", 10, "bold"), command=lambda step=number: self.select_step(step))
            title_button.pack(fill="x")
            self.step_cards.append({"frame": card, "title": title_button, "base_title": f"{number}.  {title}"})
        self.show_step(self.active_step)
        ttk.Label(self.setup_tab, text="The first administrative action asks for permission; it is then reused while this manager window stays open. Steam is checked before any account configuration is changed.", foreground="#555555").pack(anchor="w")

    def show_step(self, step: int) -> None:
        """Show one roomy workflow panel beside the compact progress list."""
        self.active_step = step
        for number, card in enumerate(self.step_cards, start=1):
            card["frame"].configure(highlightbackground="#2563eb" if number == step else "#d1d5db",
                                    highlightthickness=2 if number == step else 1)
        for child in self.step_detail.winfo_children():
            child.destroy()
        self.detail_canvas.yview_moveto(0)
        content = ttk.Frame(self.step_detail)
        content.pack(fill="both", expand=True)
        headings = {
            1: ("👥  Choose people", "Select the Linux accounts that should use this shared library. The current desktop account is selected by default."),
            2: ("📁  Shared game folder", "Prepare the dedicated folder and grant the selected people shared-library access."),
            3: ("↪  Restart session", "Sign out of Ubuntu and back in so the selected current account receives its new shared-library group membership."),
            4: ("⬇  Add the folder and Proton", "One selected person registers the shared folder with Steam and installs Proton there once."),
            5: ("⚙  Set up selected people", "Install the personal-settings tool for the people chosen in Step 1."),
            6: ("🏁  Finish and check games", "Confirm every selected person's Steam setup, then review installed games and personal setups."),
        }
        title, description = headings[step]
        ttk.Label(content, text=title, font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(content, text=description, wraplength=590, foreground="#555555").pack(anchor="w", pady=(4, 14))
        if step == 1:
            ttk.Label(content, text="Select the Linux accounts that should use this library:").pack(anchor="w")
            selector = tk.Listbox(content, selectmode="multiple", exportselection=False, height=min(max(len(self.user_names), 3), 10))
            for index, name in enumerate(self.user_names):
                selector.insert("end", name)
                if name in self.selected_names:
                    selector.selection_set(index)
            selector.pack(anchor="w", fill="x", pady=(5, 8))

            def use_people() -> None:
                self.selected_names = {str(selector.get(index)) for index in selector.curselection()}
                self.save_state()
                self.update_journey()

            ttk.Button(content, text="Use selected people", command=use_people).pack(anchor="w")
            ttk.Label(content, text="Preparing a new shared folder in Step 2 adds these people to its access group. At least the current desktop account must be selected before using Steam in Step 4.", wraplength=590).pack(anchor="w", pady=(12, 0))
        elif step == 2:
            ttk.Label(content, text="Shared folder path:").pack(anchor="w")
            path_entry = ttk.Entry(content, textvariable=self.library, width=62)
            path_entry.pack(anchor="w", fill="x", pady=(4, 8))
            path_entry.bind("<Return>", lambda _event: self.use_library_path(self.library.get()))
            buttons = ttk.Frame(content)
            buttons.pack(anchor="w")
            ttk.Button(buttons, text="Use this folder", command=lambda: self.use_library_path(self.library.get())).pack(side="left")
            ttk.Button(buttons, text="Choose folder…", command=self.manage_library).pack(side="left", padx=(8, 0))
            ttk.Label(content, text="The prefilled path is only a suggestion. Use or choose it to validate the folder; Step 2 turns green after it is confirmed ready.", wraplength=590).pack(anchor="w", pady=(12, 0))
            if not self.status_checked:
                ttk.Label(content, text="Shared access has not been checked since this manager was started. Use Refresh full status to verify it; the gray marker does not mean the saved folder or people were lost.",
                          foreground="#555555", wraplength=590).pack(anchor="w", pady=(12, 0))
            problems = [str(problem) for problem in self.status.get("shared_access_problems", [])]
            if problems:
                ttk.Label(content, text="Shared access is not ready:\n• " + "\n• ".join(problems), foreground="#991b1b", justify="left", wraplength=590).pack(anchor="w", pady=(12, 0))
                ttk.Button(content, text="Repair shared access…", command=self.offer_shared_access_repair).pack(anchor="w", pady=(10, 0))
        elif step == 3:
            current = self.current_user_name()
            if current not in self.selected_names:
                message = ("Select the current desktop account (“" + current + "”) in Step 1 before continuing. "
                           "Steam must run as an account with access to the shared folder.")
            elif not self.current_user_configured_in_group():
                message = ("“" + current + "” has not yet been granted shared-library access. Return to Step 2 and use the folder again; it will offer to grant access.")
            elif self.current_session_has_group():
                message = ("Current session: ready. “" + current + "” has steamgames in its effective groups. Continue to Step 4.")
            else:
                message = ("“" + current + "” is configured for shared-library access, but this running desktop session does not have the new group yet. "
                           "Close Steam, then fully restart the user session. If logging out/in leaves this message unchanged, reboot the computer. "
                           "Afterwards, open a new terminal and check that `id -nG` includes steamgames before starting Steam.")
            ttk.Label(content, text=message, wraplength=590).pack(anchor="w")
            ttk.Button(content, text="Recheck this session", command=lambda: self.update_journey()).pack(anchor="w", pady=(12, 0))
        elif step == 4:
            report = {str(user["name"]): user for user in self.status.get("users", [])}
            unsupported = [name for name in self.selected_users()
                           if report.get(name, {}).get("steam_client") in ("snap", "flatpak")
                           and "native" not in report.get(name, {}).get("steam_clients", [])]
            mixed = [name for name in self.selected_users()
                     if report.get(name, {}).get("steam_client") == "multiple"]
            if unsupported:
                ttk.Label(content, text="Cannot continue with " + ", ".join(unsupported)
                          + ": install and start the native Debian/Ubuntu Steam client first. Snap and Flatpak cannot use this shared system library.",
                          foreground="#991b1b", wraplength=590).pack(anchor="w", pady=(0, 10))
            elif mixed:
                ttk.Label(content, text="Both native and sandboxed Steam installations are present for " + ", ".join(mixed)
                          + ". Use the native client (/usr/games/steam), not Snap or Flatpak, for this shared library.",
                          foreground="#991b1b", wraplength=590).pack(anchor="w", pady=(0, 10))
            found = self.find_base_proton()
            if found:
                ttk.Label(content, text="Detected official Proton: " + found, wraplength=590).pack(anchor="w")
            instructions = (
                "Proton is Steam's compatibility tool for Windows-only games on Linux. Install it once in the shared folder.\n\n"
                "1. Choose one person from Step 1. If they were just added to the shared-game group, log out of Ubuntu and back in first.\n\n"
                "2. Start the native Steam client and sign in. For a new Steam account, let Steam complete its first-time setup.\n\n"
                "Use the illustrated Steam guide below. Its final card previews the account-specific setting you will apply after Step 5."
            )
            ttk.Label(content, text=instructions, wraplength=590, justify="left").pack(anchor="w", pady=(10 if found else 0, 10))
            self.build_steam_visual_guide(content)
            ttk.Label(
                content,
                text="The manager does not continuously check Steam. After installing Proton, return here and refresh; Step 4 turns green when Proton is found.",
                foreground="#555555",
                wraplength=590,
            ).pack(anchor="w", pady=(10, 8))
            ttk.Button(content, text="Refresh full status", command=self.refresh_status).pack(anchor="w")
        elif step == 5:
            selected = ", ".join(self.selected_users()) or "No people selected yet"
            ttk.Label(content, text="Selected: " + selected, wraplength=590).pack(anchor="w", pady=(0, 10))
            report = {str(user["name"]): user for user in self.status.get("users", [])}
            if self.selected_users() and self.status_checked:
                columns = ("person", "group", "steam", "tool", "storage")
                table = ttk.Treeview(content, columns=columns, show="headings", height=min(len(self.selected_users()), 5))
                table.tag_configure("ready", background="#dcfce7", foreground="#166534")
                for column, heading in (
                    ("person", "Person"),
                    ("group", "Shared group"),
                    ("steam", "Steam"),
                    ("tool", "Wrapper"),
                    ("storage", "Default storage"),
                ):
                    table.heading(column, text=heading)
                    table.column(column, anchor="center", width=115)
                table.column("person", anchor="w", width=135)
                for name in self.selected_users():
                    user = report.get(name, {})
                    client = user.get("steam_client")
                    if client == "snap":
                        steam = "Snap (unsupported)"
                    elif client == "flatpak":
                        steam = "Flatpak (unsupported)"
                    elif client == "multiple":
                        steam = "Native + sandboxed"
                    else:
                        steam = "Running" if user.get("steam_running") else "Closed" if user.get("steam_initialized") else "Not started"
                    ready = bool(user.get("in_group") and user.get("steam_initialized") and not user.get("steam_running"))
                    table.insert("", "end", values=(name, "Yes" if user.get("in_group") else "No", steam,
                                                       "Installed" if user.get("tool_installed") else "Not installed",
                                                       "Shared folder" if user.get("library_default") else "Other"),
                                 tags=("ready",) if ready else ())
                table.pack(anchor="w", fill="x", pady=(0, 10))
            elif self.selected_users():
                ttk.Label(content, text="Their current setup has not been checked yet. Use Refresh full status to inspect it.", wraplength=590).pack(anchor="w", pady=(0, 10))
            ready = [name for name in self.selected_users() if report.get(name, {}).get("steam_initialized")]
            not_started = [name for name in self.selected_users() if not report.get(name, {}).get("steam_initialized")]
            if not_started:
                ttk.Label(content, text="Before this step, each of these people must log in to the native Steam client once and let its first-time setup finish: "
                          + ", ".join(not_started) + ". They can then close Steam and return here to refresh status.",
                          foreground="#991b1b", wraplength=590).pack(anchor="w", pady=(0, 10))
            ttk.Checkbutton(
                content,
                text="Register the shared folder and make it the default Steam storage for these people",
                variable=self.make_library_default,
                command=self.update_journey,
            ).pack(anchor="w", pady=(0, 4))
            ttk.Label(
                content,
                text="Recommended. This is enabled by default so future game and tool installs use "
                     + self.library.get()
                     + ". Steam configuration files are backed up before they are changed.",
                foreground="#555555",
                wraplength=590,
            ).pack(anchor="w", pady=(0, 10))
            if ready:
                label = "Set up selected people" if len(ready) == len(self.selected_users()) else "Set up ready people (" + ", ".join(ready) + ")"
                ttk.Button(content, text=label, command=lambda people=ready: self.add_users(people)).pack(anchor="w")
            else:
                ttk.Label(content, text="No selected person has started native Steam yet, so there is nothing to configure in this step.", wraplength=590).pack(anchor="w")
            running = [name for name in self.selected_users() if report.get(name, {}).get("steam_running")]
            if running:
                ttk.Button(content, text="Close Steam for selected people", command=self.close_steam).pack(anchor="w", pady=(8, 0))
                ttk.Button(content, text="Force stop Steam for selected people…", command=self.force_close_steam).pack(anchor="w", pady=(6, 0))
            ttk.Label(content, text="Step 4 must detect an official Proton before this setup can run.", wraplength=590).pack(anchor="w", pady=(12, 0))
        else:
            report = {str(user["name"]): user for user in self.status.get("users", [])}
            if not self.status_checked:
                ttk.Label(content, text="Refresh full status to check completion for each selected person.", foreground="#555555").pack(anchor="w")
            elif not self.selected_users():
                ttk.Label(content, text="Choose people in Step 1 to check setup completion.", foreground="#555555").pack(anchor="w")
            else:
                columns = ("person", "storage", "default", "wrapper", "status")
                table = ttk.Treeview(content, columns=columns, show="headings", height=min(len(self.selected_users()), 5))
                table.tag_configure("complete", background="#dcfce7", foreground="#166534")
                for column, heading, width in (
                    ("person", "Person", 120),
                    ("storage", "Shared folder", 105),
                    ("default", "Default storage", 110),
                    ("wrapper", "Wrapper", 105),
                    ("status", "Next action", 190),
                ):
                    table.heading(column, text=heading)
                    table.column(column, anchor="center", width=width)
                table.column("person", anchor="w")
                complete = True
                for name in self.selected_users():
                    user = report.get(name, {})
                    storage = "Added" if user.get("library_registered") else "Not added"
                    default = "Shared folder" if user.get("library_default") else "Other"
                    wrapper = "Selected" if user.get("personal_tool_selected") else "Not selected"
                    if not user.get("steam_initialized"):
                        next_action = "Start native Steam once"
                    elif not user.get("tool_installed"):
                        next_action = "Complete Step 5"
                    elif not user.get("library_registered"):
                        next_action = "Add shared folder"
                    elif self.make_library_default.get() and not user.get("library_default"):
                        next_action = "Make shared folder default"
                    elif not user.get("personal_tool_selected"):
                        next_action = "Select wrapper in Steam"
                    else:
                        next_action = "Complete"
                    done = next_action == "Complete"
                    complete = complete and done
                    table.insert("", "end", values=(name, storage, default, wrapper, next_action), tags=("complete",) if done else ())
                table.pack(anchor="w", fill="x", pady=(0, 10))
                if complete:
                    ttk.Label(content, text="Setup complete. Each person can now use the shared library; a private Proton prefix is created only when they first launch a Windows game.",
                              foreground="#166534", wraplength=590).pack(anchor="w", pady=(0, 10))
                else:
                    ttk.Label(content, text="Complete the listed actions, then refresh full status. The finish flag turns green when every selected person is complete.",
                              foreground="#555555", wraplength=590).pack(anchor="w", pady=(0, 10))
            ttk.Button(content, text="Show Steam account steps", command=self.show_steam_steps).pack(anchor="w")
            ttk.Button(content, text="Refresh full status", command=self.refresh_status).pack(anchor="w", pady=(8, 0))
            ttk.Button(content, text="View games and personal setups", command=self.show_games).pack(anchor="w", pady=(8, 0))
            ttk.Separator(content).pack(fill="x", pady=16)
            ttk.Label(content, text="Advanced maintenance", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
            ttk.Label(content, text="For an existing library only: replace explicit official-Proton choices with this tool. Steam must be closed; a backup is made first.", wraplength=590).pack(anchor="w", pady=(4, 8))
            ttk.Button(content, text="Migrate explicit official-Proton choices", command=self.migrate_users).pack(anchor="w")

    def build_steam_visual_guide(self, parent: tk.Misc) -> None:
        """Build the illustrated Steam hand-off as a small previous/next flow."""
        slides = (
            (
                "1. Add the shared storage folder",
                "Open Steam → Settings → Storage, choose Add Drive, and select:\n"
                + self.library.get()
                + "\n\nIf Steam does not ask where to install Proton, open this folder's ••• menu and make it the default temporarily.",
                HERE / "media/gui/steam_storage.png",
            ),
            (
                "2. Find an official Proton",
                "Open Library, open its filter, and enable Tools. Select Proton Experimental (or another official Proton) to open its library page.",
                HERE / "media/gui/steam_tools.png",
            ),
            (
                "3. Install Proton in the shared folder",
                "Choose Install, select "
                + self.library.get()
                + " under Install To, then choose Install in the confirmation window. Do not select the home-folder library.",
                HERE / "media/gui/steam_proton_install.png",
            ),
            (
                "4. Later, select the personal-settings tool",
                "Do this after Step 5 for each person's Steam account. Open Steam → Settings → Compatibility, enable Steam Play if requested, and choose “Shared library – personal settings (Proton)” as the default compatibility tool.",
                HERE / "media/gui/steam_compatibility.png",
            ),
        )
        guide = ttk.LabelFrame(parent, text="Illustrated Steam guide", padding=10)
        guide.pack(anchor="w", fill="x", pady=(2, 0))
        title = ttk.Label(guide, font=("TkDefaultFont", 11, "bold"))
        title.pack(anchor="w")
        description = ttk.Label(guide, justify="left", wraplength=570)
        description.pack(anchor="w", fill="x", pady=(4, 8))
        image_label = ttk.Label(guide, anchor="center")
        image_label.pack(fill="x")
        controls = ttk.Frame(guide)
        controls.pack(fill="x", pady=(9, 0))
        position = ttk.Label(controls, anchor="center")
        position.pack(side="left", fill="x", expand=True)
        previous_button = ttk.Button(controls, text="← Previous")
        previous_button.pack(side="left")
        next_button = ttk.Button(controls, text="Next →")
        next_button.pack(side="left", padx=(8, 0))

        photos: list[tk.PhotoImage | None] = []
        for _slide_title, _slide_text, image_path in slides:
            try:
                photos.append(tk.PhotoImage(file=str(image_path)))
            except tk.TclError:
                photos.append(None)
        current = 0

        def show_slide(index: int) -> None:
            nonlocal current
            current = index
            slide_title, slide_text, _image_path = slides[index]
            title.configure(text=slide_title)
            description.configure(text=slide_text)
            photo = photos[index]
            if photo is None:
                image_label.configure(image="", text="Screenshot unavailable")
            else:
                image_label.configure(image=photo, text="")
            image_label.image = photo  # type: ignore[attr-defined]
            position.configure(text=f"{index + 1} of {len(slides)}")
            previous_button.configure(state="disabled" if index == 0 else "normal")
            next_button.configure(state="disabled" if index == len(slides) - 1 else "normal")

        previous_button.configure(command=lambda: show_slide(current - 1))
        next_button.configure(command=lambda: show_slide(current + 1))
        show_slide(0)

    def manage_library(self) -> None:
        """Open the system directory picker for the shared game folder."""
        selected = filedialog.askdirectory(parent=self, initialdir=self.library.get() or "/")
        if selected:
            self.use_library_path(selected)

    def use_library_path(self, path: str) -> None:
        """Inspect a chosen path; only safe path types are eligible for changes."""
        selected = path.strip()
        if not selected.startswith("/"):
            messagebox.showerror("Invalid folder", "The shared-game-folder path must be absolute.", parent=self)
            return
        if selected == "/" or selected == "/usr" or selected.startswith("/usr/"):
            messagebox.showerror("Unsafe folder", "Choose a dedicated data folder, not / or /usr.", parent=self)
            return
        if selected != self.library.get():
            # Do not show a previous folder's scan as if it described this one.
            self.status = {}
            self.status_checked = False
        self.library.set(selected)
        self.save_state()
        self.update_journey()
        candidate = Path(selected)
        if candidate.exists() and not candidate.is_dir():
            messagebox.showerror("Invalid folder", "The selected path exists but is not a directory.", parent=self)
            return
        if candidate.is_dir() and (candidate / "steamapps").is_dir():
            self.log_message("Checking existing shared Steam library: " + selected)
            self.refresh_status(after=self.offer_selected_access_then_repair)
            return
        if candidate.is_dir():
            try:
                nonempty = any(candidate.iterdir())
            except OSError:
                self.log_message("Checking protected folder with administrator access: " + selected)
                self.refresh_status()
                return
            if nonempty:
                messagebox.showerror("Folder is not safe to prepare", "This non-empty folder is not recognisably a Steam library, so the manager will not change it. Choose an empty dedicated folder instead.", parent=self)
                return
        self.create_library()

    def selected_users(self) -> list[str]:
        return sorted(self.selected_names)

    def populate_user_choices(self) -> None:
        """Populate account names without requiring an administrator scan."""
        self.user_names = [entry.pw_name for entry in pwd.getpwall()
                           if entry.pw_uid >= 1000 and not entry.pw_shell.endswith(("nologin", "false"))]
        self.selected_names.intersection_update(self.user_names)
        current = self.current_user_name()
        if not self.selected_names and current in self.user_names:
            self.selected_names.add(current)
        self.save_state()

    @staticmethod
    def current_user_name() -> str:
        return pwd.getpwuid(os.getuid()).pw_name

    @staticmethod
    def current_user_configured_in_group() -> bool:
        """Check account configuration; a long-lived terminal may keep stale groups."""
        try:
            group = grp.getgrnam(GROUP)
            user = pwd.getpwuid(os.getuid())
            return group.gr_gid in os.getgrouplist(user.pw_name, user.pw_gid)
        except KeyError:
            return False

    @staticmethod
    def current_session_has_group() -> bool:
        try:
            return grp.getgrnam(GROUP).gr_gid in os.getgroups()
        except KeyError:
            return False

    def log_message(self, message: str, level: str = "info") -> None:
        self.append_diagnostic(message)
        self.log.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.insert("end", timestamp + "  ", "muted")
        self.log.insert("end", message.rstrip() + "\n", level)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        """Clear only the visible panel; the private diagnostic log remains."""
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.log_message("Visible log cleared. The persistent diagnostic log was not changed.", "muted")

    def copy_log(self) -> None:
        text = self.log.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def begin_activity(self, message: str) -> None:
        """Show that a background action is alive rather than a frozen GUI."""
        self.active_tasks += 1
        self.activity_label.configure(text=message)
        self.activity.grid()
        self.activity_bar.start(12)

    def end_activity(self) -> None:
        self.active_tasks = max(0, self.active_tasks - 1)
        if self.active_tasks == 0:
            self.activity_bar.stop()
            self.activity.grid_remove()

    def run_admin_async(self, script: str, arguments: list[str], done, activity: str | None = None) -> None:
        """Use one narrowly-scoped pkexec helper for this open GUI window."""
        if self.active_tasks:
            self.log_message("REQUEST NOT STARTED → another action is still running.", "error")
            messagebox.showinfo(
                "Action already running",
                "Wait for the current action to finish before starting another one.",
                parent=self,
            )
            return
        display = format_admin_request(script, arguments)
        self.log_message("ADMIN REQUEST → " + display, "command")
        self.begin_activity(activity or "Working…")

        def worker() -> None:
            try:
                with self.admin_lock:
                    if self.admin_process is None or self.admin_process.poll() is not None:
                        helper = HERE / "commands" / "gui-admin-session.py"
                        self.admin_process = subprocess.Popen(
                            [PKEXEC, SYSTEM_PYTHON, str(helper)], text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                        )
                    process = self.admin_process
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(json.dumps({"command": script, "arguments": arguments}) + "\n")
                    process.stdin.flush()
                    response = process.stdout.readline()
                if not response:
                    code = 127
                    output = "Permission was cancelled or the administrator session ended without a response."
                else:
                    try:
                        payload = json.loads(response)
                        code = int(payload.get("code", 127))
                        output = str(payload.get("output", "Administrator helper returned no details."))
                    except (ValueError, json.JSONDecodeError):
                        code = 127
                        output = "Administrator helper returned an unexpected response:\n" + response.rstrip()
            except OSError as error:
                code, output = 127, str(error)

            def finish() -> None:
                self.end_activity()
                done(code, output)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def close_window(self) -> None:
        if self.active_tasks:
            messagebox.showwarning(
                "Action still running",
                "Wait for the current action to finish before closing the manager. "
                "This prevents an administrative operation from being left without its controlling session.",
                parent=self,
            )
            return
        if self.admin_process is not None:
            try:
                if self.admin_process.stdin is not None:
                    self.admin_process.stdin.close()
                try:
                    self.admin_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.admin_process.terminate()
                    try:
                        self.admin_process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.admin_process.kill()
                        self.admin_process.wait(timeout=2)
            except OSError:
                pass
        self.destroy()

    def run_admin(self, script: str, arguments: list[str], success: str, step: int | None = None,
                  parent: tk.Misc | None = None, after_success=None) -> None:
        dialog_parent = parent or self

        def complete(code: int, output: str) -> None:
            exit_level = "success" if code == 0 else "error"
            self.log_message(f"EXIT {code}", exit_level)
            self.log_message(output or ("Completed with no output." if code == 0 else "Failed with no output."), exit_level)
            if code == 0:
                if step is not None:
                    self.failed_steps.discard(step)
                messagebox.showinfo("Completed", success, parent=dialog_parent)
                self.update_journey()
                if after_success is not None:
                    after_success()
                else:
                    self.log_message("Use Refresh full status when you want to confirm the new state.")
            else:
                if step is not None:
                    self.failed_steps.add(step)
                    self.update_journey()
                messagebox.showerror("Action failed", "The action did not complete. Details are in the log below.", parent=dialog_parent)

        activity = ("Requesting Steam shutdown; checking every 5 seconds…"
                    if script == "close-steam.sh" else "Working with administrator permission…")
        self.run_admin_async(script, arguments, complete, activity)

    def refresh_status(self, after=None) -> None:
        library = self.library.get().strip()
        if not library.startswith("/"):
            messagebox.showerror("Invalid library", "The shared-library path must be absolute.", parent=self)
            return

        def complete(code: int, output: str) -> None:
            if code != 0:
                self.status_label.configure(text="Status refresh failed — see activity log", foreground="#991b1b")
                self.log_message(f"EXIT {code}", "error")
                self.log_message(output or "Status check failed with no output.", "error")
                messagebox.showerror(
                    "Status refresh failed",
                    "The status check did not complete. Details are in the activity log.",
                    parent=self,
                )
                return
            try:
                self.status = json.loads(output)
            except json.JSONDecodeError:
                self.status_label.configure(text="Invalid status response — see activity log", foreground="#991b1b")
                self.log_message("Could not read the status report:\n" + output, "error")
                return
            self.log_message("EXIT 0", "success")
            self.populate_status()
            self.status_checked = True
            self.status_label.configure(text="Full status checked: " + datetime.now().strftime("%H:%M:%S"), foreground="#166534")
            self.update_journey()
            problems = [str(problem) for problem in self.status.get("shared_access_problems", [])]
            if problems:
                self.log_message("Shared access is not ready:\n- " + "\n- ".join(problems))
            self.log_message("Status refreshed.")
            if after is not None:
                after()

        self.run_admin_async("status.py", ["--library", library, "--group", GROUP], complete,
                             "Refreshing shared-library status…")

    def offer_shared_access_repair(self) -> None:
        """Offer a narrow repair only for a recognised Steam library."""
        if self.status.get("library_kind") != "steam_library" or self.status.get("shared_access_ready"):
            return
        path = self.library.get()
        text = ("This is an existing Steam library, but its shared Linux access needs repair. Close Steam for all users first.\n\n"
                "Repair group ownership, group read/write access, setgid directories, and default ACLs inside:\n\n"
                + path + "\n\nThis does not delete or download games, and it will not follow links outside this library.")
        if not messagebox.askyesno("Repair shared access", text, parent=self):
            return

        def repair() -> None:
            self.run_admin("repair-shared-library.sh", ["--library", path, "--group", GROUP],
                           "Shared-library access was repaired. The status will now be refreshed.", 2,
                           after_success=self.refresh_status)

        # The repair deliberately refuses to change files while any native
        # Steam client is open.  Offer the in-manager clean shutdown so a
        # permission repair is a complete, testable flow rather than an error
        # followed by a hunt for the close button in another step.
        running = [str(user["name"]) for user in self.status.get("users", []) if user.get("steam_running")]
        if not running:
            repair()
            return
        close_text = ("Steam is currently running for: " + ", ".join(running)
                      + ".\n\nClose those native Steam clients now, then repair shared access? "
                        "Steam will be asked to shut down cleanly; the repair will not run if one remains open.")
        if messagebox.askyesno("Close Steam before repair", close_text, parent=self):
            self.run_admin("close-steam.sh", running,
                           "Steam was closed. Repairing shared-library access now.", 2,
                           after_success=repair)

    def offer_selected_access_then_repair(self) -> None:
        """Grant selected users before asking Steam to use an existing library."""
        report = {str(user["name"]): user for user in self.status.get("users", [])}
        missing = [name for name in self.selected_users() if not report.get(name, {}).get("in_group")]
        if missing:
            text = ("The selected people do not yet have access to this shared folder:\n\n"
                    + ", ".join(missing)
                    + "\n\nGrant them access now? They must then log out and back in before Steam can use the folder.")
            if messagebox.askyesno("Grant shared-library access", text, parent=self):
                self.run_admin("grant-library-users.sh", ["--group", GROUP, *missing],
                               "Shared-library access granted. Continue with Step 3 after logging out and back in.", 2,
                               after_success=lambda: self.refresh_status(after=self.offer_shared_access_repair))
            return
        self.offer_shared_access_repair()

    def populate_status(self) -> None:
        users = self.status.get("users", [])
        self.user_names = [str(user["name"]) for user in users]
        self.selected_names.intersection_update(self.user_names)
        self.update_steam_client_warning()
        self.update_journey()

    def update_steam_client_warning(self) -> None:
        """Surface unsupported clients before users reach Steam storage setup."""
        report = {str(user["name"]): user for user in self.status.get("users", [])}
        unsupported = []
        mixed = []
        for name in self.selected_users():
            user = report.get(name, {})
            client = user.get("steam_client")
            if client in ("snap", "flatpak") and "native" not in user.get("steam_clients", []):
                unsupported.append(name + " (" + str(client).title() + ")")
            elif client == "multiple":
                mixed.append(name)
        if unsupported:
            self.steam_client_warning.configure(
                text="Unsupported Steam installation detected for " + ", ".join(unsupported)
                + ". This manager requires the native Debian/Ubuntu Steam client; Snap and Flatpak cannot use /srv/SteamLibrary.")
        elif mixed:
            self.steam_client_warning.configure(
                text="Both native and sandboxed Steam installations are present for " + ", ".join(mixed)
                + ". Launch /usr/games/steam for this shared library; do not use the Snap or Flatpak client.")
        else:
            self.steam_client_warning.configure(text="")

    def show_games(self) -> None:
        window = tk.Toplevel(self)
        window.title("🎮 Games and personal settings")
        window.transient(self)
        window.geometry("1050x470")
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="🎮  Installed games and personal game setups", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        if not self.status_checked:
            ttk.Label(frame, text="Use Refresh full status on the main window to inspect installed games and personal game setups.", foreground="#555555").pack(anchor="w", pady=(4, 8))
        else:
            ttk.Label(
                frame,
                text="Green means every selected person's current configuration is safe. "
                     "A personal setup is created only after that person launches a Windows game.",
                foreground="#555555",
            ).pack(anchor="w", pady=(4, 8))
            ttk.Label(
                frame,
                text="A shared-prefix warning means the game used shared Proton data in the past; "
                     "it does not override a currently safe personal configuration.",
                foreground="#555555",
                wraplength=980,
            ).pack(anchor="w", pady=(0, 8))
        columns = ("appid", "game", "platform", "readiness", "setups")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        tree.tag_configure("ready_for_everyone", background="#dcfce7", foreground="#166534")
        tree.tag_configure("needs_attention", background="#fee2e2", foreground="#991b1b")
        for column, title, width in (
            ("appid", "AppID", 80),
            ("game", "Game", 250),
            ("platform", "Installed files", 210),
            ("readiness", "Current configuration", 330),
            ("setups", "Personal setup created", 180),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w")
        selected = self.selected_users()
        for row in build_game_rows(self.status.get("games", []), selected):
            tree.insert("", "end", values=row.values, tags=(row.tag,) if row.tag else ())
        tree.pack(fill="both", expand=True)

    def show_steam_steps(self) -> None:
        """Explain the short account-specific part Steam cannot automate safely."""
        window = tk.Toplevel(self)
        window.title("✓ Finish setup in Steam")
        window.transient(self)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="✓  Finish setup in each person's Steam account", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Do these remaining Steam-account steps separately for each selected person. The earlier group access and wrapper installation are already complete.", wraplength=650).pack(anchor="w", pady=(4, 12))
        storage_instruction = (
            "2. Step 5 normally registers the shared folder and makes it the default automatically. "
            "If this person's status still says Not added or Other, open Steam → Settings → Storage, "
            "use + / Add Drive to choose the folder from Step 2, then use its ••• menu → Make Default.\n\n"
            if self.make_library_default.get()
            else
            "2. Open Steam → Settings → Storage. Use + / Add Drive to choose the shared game folder "
            "shown in Step 2, then use its ••• menu → Make Default if desired.\n\n"
        )
        instructions = (
            "1. Start the native Steam client and sign in as that person.\n\n"
            + storage_instruction
            + "3. Open Steam → Settings → Compatibility. Enable Steam Play if Steam asks, then choose “Shared library – personal settings (Proton)” as the default compatibility tool.\n\n"
            "4. Restart Steam. Windows games will create that person's private game settings automatically the first time they are launched. Native Linux games need no private setup."
        )
        tk.Label(frame, text=instructions, justify="left", anchor="nw", wraplength=650).pack(fill="x")

        selected = self.selected_statuses()
        if not self.selected_users():
            status = "Choose people in Step 1 to review their individual progress here."
        elif not self.status_checked:
            status = "Use Refresh full status to confirm each person's progress after these steps."
        else:
            progress = []
            for user in selected:
                completed = (
                    user["library_registered"]
                    and user["personal_tool_selected"]
                    and (not self.make_library_default.get() or user.get("library_default"))
                )
                progress.append(str(user["name"]) + (": completed" if completed else ": still needs Steam steps"))
            status = "Current progress: " + "; ".join(progress)
        ttk.Label(frame, text=status, foreground="#555555", wraplength=650).pack(anchor="w", pady=(12, 0))
        ttk.Button(frame, text="Close", command=window.destroy).pack(anchor="e", pady=(12, 0))

    def selected_statuses(self) -> list[dict[str, object]]:
        selected = set(self.selected_users())
        return [user for user in self.status.get("users", []) if user["name"] in selected]

    def set_card(self, number: int, color: str) -> None:
        card = self.step_cards[number - 1]
        for key in ("frame", "title"):
            card[key].configure(background=color)
        marker = "✓" if color == "#dcfce7" else "⚠" if color == "#fee2e2" else "○"
        card["title"].configure(text=f"{marker}  {card['base_title']}")

    def update_journey(self) -> None:
        if not self.step_cards:
            return
        selected = bool(self.selected_users())
        session_ready = (selected and self.current_user_name() in self.selected_names
                         and self.current_user_configured_in_group() and self.current_session_has_group())
        if not self.status_checked:
            self.set_card(1, "#dcfce7" if selected else "#e5e7eb")
            self.set_card(2, "#fee2e2" if 2 in self.failed_steps else "#e5e7eb")
            self.set_card(3, "#dcfce7" if session_ready else "#e5e7eb")
            self.set_card(4, "#e5e7eb")
            self.set_card(5, "#fee2e2" if 5 in self.failed_steps else "#e5e7eb")
            self.set_card(6, "#e5e7eb")
            self.show_step(self.active_step)
            return
        library_exists = bool(self.status.get("library_exists"))
        sharing_ready = bool(self.status.get("shared_access_ready"))
        self.set_card(1, "#dcfce7" if selected else "#e5e7eb")
        if 2 in self.failed_steps:
            self.set_card(2, "#fee2e2")
        elif library_exists and sharing_ready:
            self.set_card(2, "#dcfce7")
        elif self.status.get("library_kind") == "steam_library":
            self.set_card(2, "#fee2e2")
        else:
            self.set_card(2, "#e5e7eb")

        users = self.selected_statuses()
        self.set_card(3, "#dcfce7" if session_ready else "#e5e7eb")

        if self.status.get("base_proton_ready"):
            self.set_card(4, "#dcfce7")
        else:
            self.set_card(4, "#e5e7eb")

        if 5 in self.failed_steps:
            self.set_card(5, "#fee2e2")
        elif not selected or not self.status.get("base_proton_ready"):
            self.set_card(5, "#e5e7eb")
        elif users and any(not user["steam_initialized"] for user in users):
            self.set_card(5, "#fee2e2")
        elif users and all(
            user["in_group"]
            and user["tool_installed"]
            and (not self.make_library_default.get() or user.get("library_default"))
            for user in users
        ):
            self.set_card(5, "#dcfce7")
        else:
            self.set_card(5, "#e5e7eb")

        if not selected:
            self.set_card(6, "#e5e7eb")
        elif users and all(
            user["library_registered"]
            and user["personal_tool_selected"]
            and (not self.make_library_default.get() or user.get("library_default"))
            for user in users
        ):
            self.set_card(6, "#dcfce7")
        else:
            self.set_card(6, "#e5e7eb")
        self.show_step(self.active_step)

    def create_library(self, parent: tk.Misc | None = None) -> None:
        users = self.selected_users()
        if not users:
            messagebox.showwarning("Select people first", "Choose at least one person in Step 1 before preparing a new shared folder.", parent=parent or self)
            return
        people = ", ".join(users) if users else "no people yet"
        if not messagebox.askyesno("Prepare shared folder", "Prepare this missing or empty folder for a shared Steam library:\n\n" + self.library.get() + "\n\nIt will initially include: " + people + ".\n\nNon-empty folders are refused.", parent=parent or self):
            return
        self.run_admin("setup-shared-library.sh", ["--library", self.library.get(), "--group", GROUP, *users], "Shared folder prepared. Selected people must log out and back in before using it.", 2, parent, self.refresh_status)

    def add_users(self, users: list[str] | None = None, parent: tk.Misc | None = None) -> None:
        users = users if users is not None else self.selected_users()
        if not users:
            messagebox.showwarning("Select users", "Select one or more people in Step 1 first.", parent=parent or self)
            return
        proton = self.find_base_proton()
        if proton is None:
            messagebox.showwarning("Complete Step 4 first", "Steam has not found an official Proton tool in this shared folder yet. Open Step 4 for the exact folder and installation steps.", parent=parent or self)
            return
        text = "Set up shared-library access and personal Windows-game settings for: " + ", ".join(users) + "?\n\nThe tool checks whether Steam is running and asks it to close before making changes."
        if self.make_library_default.get():
            text += "\n\nThe shared folder will also be registered and made their default Steam storage. Changed Steam configuration files are backed up first."
        if messagebox.askyesno("Configure users", text, parent=parent or self):
            arguments = ["--close-steam"]
            if self.make_library_default.get():
                arguments.extend(["--default-library", self.library.get()])
            arguments.extend(["--group", GROUP, "--base-proton", proton, *users])
            self.run_admin(
                "add-user.sh",
                arguments,
                "People configured. Each person must log out/in and complete the Steam hand-off steps.",
                5,
                parent,
                self.refresh_status,
            )

    def find_base_proton(self) -> str | None:
        """Prefer Experimental, but work with any official Proton already shared."""
        candidates = [Path(path) for path in self.status.get("available_protons", [])]
        if not candidates:
            common = Path(self.library.get()) / "steamapps/common"
            try:
                candidates = [item for item in common.iterdir() if item.is_dir() and item.name.startswith("Proton")
                              and (item / "proton").is_file() and (item / "proton").stat().st_mode & 0o111]
            except OSError:
                candidates = []
        candidates.sort(key=lambda item: (item.name != "Proton - Experimental", item.name))
        return str(candidates[0]) if candidates else None

    def close_steam(self, parent: tk.Misc | None = None) -> None:
        users = self.selected_users()
        if not users:
            messagebox.showwarning("Select people", "Select one or more names in the People list first.", parent=parent or self)
            return
        self.run_admin(
            "close-steam.sh",
            users,
            "Steam is closed for the selected people. You can continue setup.",
            parent=parent,
            after_success=self.refresh_status,
        )

    def force_close_steam(self, parent: tk.Misc | None = None) -> None:
        """Offer a deliberate fallback when Steam ignores its normal exit request."""
        users = self.selected_users()
        if not users:
            messagebox.showwarning("Select people", "Select one or more names in the People list first.", parent=parent or self)
            return
        text = ("Steam did not respond to its normal exit request for: " + ", ".join(users) + ".\n\n"
                "Send SIGTERM to Steam's main process? This can interrupt an active download or game, but does not use SIGKILL. "
                "Only use this after checking that nobody is playing.")
        if messagebox.askyesno("Force stop Steam", text, parent=parent or self):
            self.run_admin("close-steam.sh", ["--force", *users],
                           "Steam was stopped. The status will now be refreshed.",
                           parent=parent, after_success=self.refresh_status)

    def migrate_users(self, parent: tk.Misc | None = None) -> None:
        users = self.selected_users()
        if not users:
            messagebox.showwarning("Select people", "Select one or more people in Step 1 first.", parent=parent or self)
            return
        if messagebox.askyesno("Migrate mappings", "Convert existing explicit official-Proton selections for: " + ", ".join(users) + "?\n\nSteam must be closed. A backup is created first.", parent=parent or self):
            self.run_admin("migrate-existing-games.sh", ["--apply", *users], "Existing explicit Proton mappings were processed.", parent=parent)


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: launch-gui.sh\n\nStart this command from a graphical Ubuntu desktop.")
        raise SystemExit(0)
    if "--check" in sys.argv:
        print("Tkinter import: OK")
        raise SystemExit(0)
    SharedSteamGui().mainloop()
