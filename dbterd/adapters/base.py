import os
from pathlib import Path
from typing import Optional

import click

from dbterd import default
from dbterd.adapters import adapter
from dbterd.adapters.dbt_cloud.administrative import DbtCloudArtifact
from dbterd.adapters.dbt_cloud.discovery import DbtCloudMetadata
from dbterd.adapters.dbt_core.dbt_invocation import DbtInvocation
from dbterd.adapters.filter import has_unsupported_rule
from dbterd.adapters.meta import Ref, Table
from dbterd.helpers import cli_messaging, file as file_handlers
from dbterd.helpers.log import logger


class Executor:
    """Main Executor."""

    ctx: click.Context

    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.filename_manifest = "manifest.json"
        self.filename_catalog = "catalog.json"
        self.dbt: DbtInvocation = None

    def run(self, node_unique_id: Optional[str] = None, **kwargs) -> tuple[list[Table], list[Ref]]:
        """Generate ERD from files."""
        logger.info(f"Using algorithm [{kwargs.get('algo')}]")
        kwargs = self.evaluate_kwargs(**kwargs)
        return self.__run_by_strategy(node_unique_id=node_unique_id, **kwargs)

    def run_metadata(self, **kwargs) -> tuple[list[Table], list[Ref]]:
        """Generate ERD from API metadata."""
        logger.info(f"Using algorithm [{kwargs.get('algo')}]")
        kwargs = self.evaluate_kwargs(**kwargs)
        return self.__run_metadata_by_strategy(**kwargs)

    def evaluate_kwargs(self, **kwargs) -> dict:
        """
        Re-calculate the options.

        Raises:
            click.UsageError: Not Supported exception

        Returns:
            dict: kwargs dict

        """
        artifacts_dir, dbt_project_dir = self.__get_dir(**kwargs)
        command = self.ctx.command.name

        select = list(kwargs.get("select")) or []
        exclude = list(kwargs.get("exclude")) or []

        if not kwargs.get("dbt"):
            self.__check_if_any_unsupported_selection(select, exclude)

        if command == "run":
            if kwargs.get("dbt"):
                logger.info(f"Using dbt project dir at: {dbt_project_dir}")
                self.dbt = DbtInvocation(
                    dbt_project_dir=kwargs.get("dbt_project_dir"),
                    dbt_target=kwargs.get("dbt_target"),
                )
                select = self.__get_selection(**kwargs)
                exclude = []
                if not select:
                    select = ["exact:none"]  # 'cause [] is all, so let's select nothing here

                if kwargs.get("dbt_auto_artifacts"):
                    self.dbt.get_artifacts_for_erd()
                    artifacts_dir = f"{dbt_project_dir}/target"
            elif kwargs.get("dbt_cloud"):
                artifacts_dir = f"{dbt_project_dir}/target"
            logger.info(f"Using dbt artifact dir at: {artifacts_dir}")

        kwargs["artifacts_dir"] = artifacts_dir
        kwargs["dbt_project_dir"] = dbt_project_dir
        kwargs["select"] = select
        kwargs["exclude"] = exclude

        return kwargs

    def __check_if_any_unsupported_selection(self, select: Optional[list] = None, exclude: Optional[list] = None):
        """
        Throw an error if detected any supported selections
        which are built-in in dbterd (not dbt).

        Args:
            select (list, optional): Select rules. Defaults to [].
            exclude (list, optional): Exclude rules. Defaults to [].

        Raises:
            click.UsageError: Unsupported selection

        """
        if exclude is None:
            exclude = []
        if select is None:
            select = []
        rules = list(select)
        rules.extend(exclude)
        unsupported, rule = has_unsupported_rule(rules=rules)
        if unsupported:
            message = f"Unsupported Selection found: {rule}"
            logger.error(message)
            raise click.UsageError(message)

    def __get_dir(self, **kwargs) -> str:
        """
        Calculate the dbt artifact directory and dbt project directory.

        Returns:
            tuple(str, str): Path to target directory and dbt project directory

        """
        artifact_dir = f"{kwargs.get('artifacts_dir') or kwargs.get('dbt_project_dir')}"  # default
        project_dir = f"{kwargs.get('dbt_project_dir') or kwargs.get('artifacts_dir')}"  # default

        if not artifact_dir:
            return (
                default.default_artifact_path(),
                str(Path(default.default_artifact_path()).parent.absolute()),
            )

        artifact_dir = Path(artifact_dir).absolute()
        project_dir = Path(project_dir).absolute()

        if not os.path.isfile(f"{artifact_dir}/{self.filename_manifest}"):
            artifact_dir = f"{project_dir}/target"  # try child target

        return (str(artifact_dir), str(project_dir))

    def __get_selection(self, **kwargs) -> list[str]:
        """Override the Selection using dbt's one with `--dbt`."""
        if not self.dbt:
            raise click.UsageError("Flag `--dbt` need to be enabled")

        return self.dbt.get_selection(
            select_rules=kwargs.get("select"),
            exclude_rules=kwargs.get("exclude"),
        )

    def __read_manifest(self, mp: str, mv: Optional[int] = None):
        """
        Read the Manifest content.

        Args:
            mp (str): manifest.json json file path
            mv (int, optional): Manifest version. Defaults to None.

        Returns:
            dict: Manifest dict

        """
        cli_messaging.check_existence(mp, self.filename_manifest)
        conditional = f" or provided version {mv} is incorrect" if mv else ""
        with cli_messaging.handle_read_errors(self.filename_manifest, conditional):
            return file_handlers.read_manifest(path=mp, version=mv)

    def __read_catalog(self, cp: str, cv: Optional[int] = None):
        """
        Read the Catalog content.

        Args:
            cp (str): catalog.json file path
            cv (int, optional): Catalog version. Defaults to None.

        Returns:
            dict: Catalog dict

        """
        cli_messaging.check_existence(cp, self.filename_catalog)
        with cli_messaging.handle_read_errors(self.filename_catalog):
            return file_handlers.read_catalog(path=cp, version=cv)

    def __get_operation(self, kwargs):
        """
        Get target's operation (aka.`parse` function).

        Returns:
            func: Operation function

        """
        target = adapter.load_target(name=kwargs["target"])  # import {target}
        return target.run

    def __save_result(self, path, data):
        """
        Save ERD data to file.

        Args:
            path (str): Output file path
            data (dict): ERD data

        Raises:
            click.FileError: Can not save the file

        """
        try:
            file_path = f"{path}/{data[0]}"
            with open(file_path, "w") as f:
                logger.info(f"Output saved to {file_path}")
                f.write(data[1])
        except Exception as e:
            logger.error(str(e))
            raise click.FileError(f"Could not save the output: {e!s}") from e

    def __set_single_node_selection(self, manifest, node_unique_id: str, type: Optional[str] = None, **kwargs) -> dict:
        """
        Override the Selection for the specific manifest node.

        Args:
            manifest (Union[Manifest, dict]): Manifest data of dbt project
            node_unique_id (str): Manifest node unique ID
            type (str, optional): |
                Determine manifest type e.g. from file or from metadata.
                Defaults to None.

        Returns:
            dict: Edited kwargs dict

        """
        if not node_unique_id:
            return kwargs

        algo_module = adapter.load_algo(name=kwargs["algo"])
        kwargs["select"] = algo_module.find_related_nodes_by_id(
            manifest=manifest, node_unique_id=node_unique_id, type=type, **kwargs
        )
        kwargs["exclude"] = []

        return kwargs

    def __run_by_strategy(self, node_unique_id: Optional[str] = None, **kwargs) -> tuple[list[Table], list[Ref]]:
        """Local File - Read artifacts and export the diagram file following the target."""
        if kwargs.get("dbt_cloud"):
            DbtCloudArtifact(**kwargs).get(artifacts_dir=kwargs.get("artifacts_dir"))

        manifest = self.__read_manifest(
            mp=kwargs.get("artifacts_dir"),
            mv=kwargs.get("manifest_version"),
        )
        catalog = self.__read_catalog(
            cp=kwargs.get("artifacts_dir"),
            cv=kwargs.get("catalog_version"),
        )

        if node_unique_id:
            kwargs = self.__set_single_node_selection(manifest=manifest, node_unique_id=node_unique_id, **kwargs)
        operation = self.__get_operation(kwargs)
        result = operation(manifest=manifest, catalog=catalog, **kwargs)

        if not kwargs.get("api"):
            self.__save_result(path=kwargs.get("output"), data=result)

        return result[1]

    def __run_metadata_by_strategy(self, **kwargs) -> tuple[list[Table], list[Ref]]:
        """Metadata - Read artifacts and export the diagram file following the target."""
        data = DbtCloudMetadata(**kwargs).query_erd_data()
        operation = self.__get_operation(kwargs)

        result = operation(manifest=data, catalog="metadata", **kwargs)

        if not kwargs.get("api"):
            self.__save_result(path=kwargs.get("output"), data=result)

        return result[1]
