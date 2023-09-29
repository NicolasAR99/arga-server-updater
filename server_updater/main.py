import json
import os
import shutil
import sys
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from urllib import request

from server_updater.config import (
    A3_SERVER_ID,
    A3_MODS_DIR,
    A3_WORKSHOP_DIR,
    A3_MOD_KEYS_SOURCE_DIRECTORY,
    A3_MOD_KEYS_DESTINATION_DIRECTORY,
    WORKSHOP_CHANGELOG_URL,
    PATTERN,
    REFORGER_SERVER_ID,
    REFORGER_ARMA_BINARY,
    REFORGER_ARMA_PROFILE,
    REFORGER_ARMA_MAX_FPS,
    REFORGER_ARMA_CONFIG,
    REFORGER_ARMA_ARMA_PARAMS,
    REFORGER_SERVER_DIR,
    A3_MOD_DEFAULT,
    A3_MODS_LIST_PATH,
)
from server_updater.constants import UpdateType, Server
from server_updater.dtos import ServerConfig
from server_updater.log import Log
from server_updater.steamcmd import SteamCmd


class ServerUpdater:
    def __init__(
        self,
        logger: Log,
        steamcmd: SteamCmd,
        server: Server,
        mods_list_name: Optional[str] = None,
    ):
        self._logger = logger
        self._steamcmd = steamcmd
        self._server = server
        self._mods_list_name = mods_list_name
        self._config = self._set_config()

    def run(self) -> None:
        while True:
            selected = self._input()
            try:
                self.run_choice(selected=selected)
            except KeyError:
                self._default()

    def run_choice(self, selected: str) -> None:
        choices = self._get_choices()
        choices[self._server][selected.lower()]()

    def _input(self) -> str:
        return input(self._get_input_choices())

    def _get_input_choices(self) -> str:
        input_map = {
            Server.A3: """
                      A: Update server and Mods
                      B: Update Mods only
                      C: Update Server only
                      D: Create mod symlinks
                      E: Lower case mods
                      F: Copy key files
                      Q: Quit/Log Out
                      Please enter your choice: """,
            Server.REFORGER: """
                      A: Update Server and run Reforger
                      B: Run Reforger server
                      C: Update Server only
                      Q: Quit/Log Out
                      Please enter your choice: """,
        }
        return input_map[self._server]

    def _get_choices(self) -> Dict[Server, Dict[str, Any]]:
        return {
            Server.A3: {
                "a": self._update_server_and_mods,
                "b": self._update_mods_only,
                "c": self._update_server,
                "d": self._create_mod_symlinks,
                "e": self._lower_case_mods,
                "f": self._copy_key_files,
                "q": self._quit,
            },
            Server.REFORGER: {
                "a": self._update_server_and_run_reforger,
                "b": self._run_reforger_server,
                "c": self._update_server,
                "q": self._quit,
            },
        }

    def _update_server_and_mods(self):
        self._update_server()
        self._update_mods_only()

    def _update_mods_only(self) -> None:
        mods_to_update = self._get_mods(self._mods_list_name)
        updated_mods = self._update_mods(mods_to_update)
        if updated_mods is None:
            self._logger.log("All MODs are updated")
            print()
            return None
        self._lower_case_mods(updated_mods)
        self._create_mod_symlinks(updated_mods)
        self._copy_key_files(updated_mods)

    def _update_server(self) -> None:
        self._logger.log(
            f"Updating {self._server.value} server ({self._config.server_id})"
        )
        self._steamcmd.run(update_type=UpdateType.SERVER)

    def _create_mod_symlinks(self, updated_mods: Dict[str, str]) -> None:
        self._logger.log("Creating symlinks...")
        for mod_name, mod_id in updated_mods.items():
            link_path = f"{A3_MODS_DIR}/{mod_name}"
            real_path = f"{A3_WORKSHOP_DIR}/{mod_id}"

            if not os.path.isdir(real_path):
                print(f"Mod '{mod_name}' does not exist! ({real_path})")
                continue
            if os.path.islink(link_path):
                continue
            os.symlink(real_path, link_path)
            print(f"Creating symlink '{link_path}'...")
        print()

    def _lower_case_mods(self, updated_mods: Dict[str, str]) -> None:
        self._logger.log("Converting uppercase files/folders to lowercase...")
        for value in updated_mods.values():
            directory_path = f"{A3_WORKSHOP_DIR}/{value}"
            for root, dirs, files in os.walk(directory_path):
                for filename in files + dirs:
                    new_name = os.path.join(root, filename).lower()
                    if new_name == os.path.join(root, filename):
                        continue
                    try:
                        os.rename(os.path.join(root, filename), new_name)
                        print(f'Renamed: {filename} -> {new_name}')
                    except OSError as e:
                        print(f'Error renaming {filename}: {e}')

    @staticmethod
    def _mod_needs_update(mod_id, path) -> bool:
        if not os.path.isdir(path):
            return False
        response = request.urlopen(f"{WORKSHOP_CHANGELOG_URL}/{mod_id}").read()
        response = response.decode("utf-8")
        match = PATTERN.search(response)

        if not match:
            return False
        updated_at = datetime.fromtimestamp(int(match.group(1)))
        created_at = datetime.fromtimestamp(os.path.getctime(path))
        return updated_at >= created_at

    def _update_mods(self, mods_to_update: Dict[str, str]) -> Optional[Dict[str, str]]:
        updated_mods = {}
        for mod_name, mod_id in mods_to_update.items():
            mod_path = f"{A3_WORKSHOP_DIR}/{mod_id}"
            is_dir, mod_needs_update = self._delete_mod_if_needed(mod_id=mod_id, mod_path=mod_path)
            if is_dir and not mod_needs_update:
                print(f'No update required for "{mod_name}" ({mod_id})... SKIPPING')
                continue
            if self._try_to_update_mod(mod_id=mod_id, mod_name=mod_name, is_dir=is_dir):
                updated_mods[mod_name] = mod_id
        return updated_mods if updated_mods != {} else None

    def _try_to_update_mod(self, mod_id: str, mod_name: str, is_dir: bool) -> bool:
        tries = 0
        max_tries = 10
        while not is_dir and tries < 10:
            self._logger.log(f'Updating "{mod_name}" ({mod_id}) | {tries + 1}')
            self._steamcmd.run(update_type=UpdateType.MOD, mod_id=int(mod_id))
            time.sleep(5)
            tries += 1

        if tries >= max_tries:
            self._logger.log(f"!! Updating {mod_name} failed after {tries} tries !!")
            return False
        return True

    def _delete_mod_if_needed(self, mod_id: str, mod_path: str) -> Tuple[bool, bool]:
        is_dir = os.path.isdir(mod_path)
        mod_needs_update = self._mod_needs_update(mod_id, mod_path)
        if not is_dir:
            return False, False
        if not self._mod_needs_update(mod_id, mod_path):
            return True, False
        shutil.rmtree(mod_path)
        return True, True

    def _copy_key_files(self, updated_mods: Dict[str, str]) -> None:
        """Copy the Mods sign files."""
        self._logger.log("Start copy of Mods sign key files...")
        was_copied = False
        for value in updated_mods.values():
            directory_path = f"{A3_MOD_KEYS_SOURCE_DIRECTORY}/{value}"
            for root_dir, _, files in os.walk(directory_path):
                for file in files:
                    if not file.endswith(".bikey"):
                        continue
                    source_file_path = os.path.join(root_dir, file)
                    destination_file_path = os.path.join(
                        A3_MOD_KEYS_DESTINATION_DIRECTORY, file
                    )
                    print(f"Copy {file} file")
                    shutil.copy(source_file_path, destination_file_path)
                    was_copied = True

        if not was_copied:
            print("There are no MODs sign key files to copy\n")
            return None
        print("MODs sign key files was successfully copied.\n")

    def _update_server_and_run_reforger(self):
        self._update_server()
        self._run_reforger_server()

    @staticmethod
    def _run_reforger_server():
        # https://community.bistudio.com/wiki/Arma_Reforger:Startup_Parameters
        launch = " ".join(
            [
                REFORGER_ARMA_BINARY,
                f"-config {REFORGER_ARMA_CONFIG}",
                "-backendlog",
                "-nothrow",
                f"-maxFPS {REFORGER_ARMA_MAX_FPS}",
                f"-profile {REFORGER_ARMA_PROFILE}",
                REFORGER_ARMA_ARMA_PARAMS,
            ]
        )
        print(launch, flush=True)
        try:
            os.system(f"(cd {REFORGER_SERVER_DIR} && {launch})")
        except OSError as e:
            print(f"Error launching the Reforger server: {e}")

    @staticmethod
    def _quit() -> None:
        print("\nClosing Program now\n")
        sys.exit()

    def _default(self) -> None:
        options = self._get_options_to_print()
        print()
        print(f"You must only select either {options} to quit.")
        print("Please try again")
        time.sleep(1)

    def _get_options_to_print(self) -> str:
        options = [o.upper() for o in self._get_choices()[self._server]]
        return f"{', '.join(options[:-1])} or {options[-1]}"

    def _set_config(self):
        if self._server == Server.A3:
            return ServerConfig(
                server_id=A3_SERVER_ID,
            )
        return ServerConfig(
            server_id=REFORGER_SERVER_ID,
        )

    def _get_mods(
        self, mods_list_name: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        if self._server == Server.REFORGER:
            return None
        if mods_list_name is None:
            mods_list_name = A3_MOD_DEFAULT

        file_name = f"{A3_MODS_LIST_PATH}/{mods_list_name}.json"

        with open(file_name, "r") as file:
            return json.load(file)
