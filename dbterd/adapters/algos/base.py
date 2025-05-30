import copy
from typing import Optional

import click

from dbterd.adapters.filter import is_selected_table
from dbterd.adapters.meta import Column, Ref, Table
from dbterd.constants import (
    DEFAULT_ALGO_RULE,
    TEST_META_IGNORE_IN_ERD,
    TEST_META_RELATIONSHIP_TYPE,
)
from dbterd.helpers.log import logger
from dbterd.types import Catalog, Manifest


# Constants
MAX_TEST_PARENTS = 2


def get_tables_from_metadata(data=None, **kwargs) -> list[Table]:
    """
    Extract tables from dbt metadata.

    Args:
        data (dict): dbt metadata query result
        **kwargs: Additional options including:
            resource_type (list): Types of resources to include (model, source, etc.)
            entity_name_format (str): Format string for entity names
            omit_columns (bool): Whether to exclude columns from tables

    Returns:
        List[Table]: All parsed tables

    """
    if data is None:
        data = []
    tables = []
    table_exposures = get_node_exposures_from_metadata(data=data, **kwargs)
    # Model
    if "model" in kwargs.get("resource_type", []):
        for data_item in data:
            for model in data_item.get("models", {}).get("edges", []):
                table = get_table_from_metadata(
                    model_metadata=model,
                    exposures=table_exposures,
                    **kwargs,
                )
                tables.append(table)

    # Source
    if "source" in kwargs.get("resource_type", []):
        for data_item in data:
            for model in data_item.get("sources", {}).get("edges", []):
                table = get_table_from_metadata(
                    model_metadata=model,
                    exposures=table_exposures,
                    **kwargs,
                )
                tables.append(table)

    return tables


def get_tables(manifest: Manifest, catalog: Catalog, **kwargs) -> list[Table]:
    """
    Extract tables from dbt artifacts.

    Args:
        manifest (dict): dbt manifest json
        catalog (dict): dbt catalog json
        **kwargs: Additional options including:
            entity_name_format (str): Format string for entity names
            omit_columns (bool): Whether to exclude columns from tables

    Returns:
        List[Table]: All tables parsed from dbt artifacts

    """
    tables = []

    table_exposures = get_node_exposures(manifest=manifest)

    if hasattr(manifest, "nodes"):
        for node_name, node in manifest.nodes.items():
            if node_name.startswith("model.") or node_name.startswith("seed.") or node_name.startswith("snapshot."):
                catalog_node = catalog.nodes.get(node_name)
                table = get_table(
                    node_name=node_name,
                    manifest_node=node,
                    catalog_node=catalog_node,
                    exposures=table_exposures,
                    **kwargs,
                )
                tables.append(table)

    if hasattr(manifest, "sources"):
        for node_name, source in manifest.sources.items():
            if node_name.startswith("source"):
                catalog_source = catalog.sources.get(node_name)
                table = get_table(
                    node_name=node_name,
                    manifest_node=source,
                    catalog_node=catalog_source,
                    exposures=table_exposures,
                    **kwargs,
                )
                tables.append(table)

    return tables


def filter_tables_based_on_selection(tables: list[Table], **kwargs) -> list[Table]:
    """
    Filter list of tables based on the Selection Rules.

    Args:
        tables (List[Table]): Parsed tables
        **kwargs: Additional options including:
            select (list): Selection rules to include tables
            exclude (list): Rules to exclude tables
            resource_type (list): Types of resources to include

    Returns:
        List[Table]: Filtered tables

    """
    return [
        table
        for table in tables
        if is_selected_table(
            table=table,
            select_rules=kwargs.get("select") or [],
            resource_types=kwargs.get("resource_type", []),
            exclude_rules=kwargs.get("exclude") or [],
        )
    ]


def enrich_tables_from_relationships(tables: list[Table], relationships: list[Ref]) -> list[Table]:
    """
    Fulfill columns in Table due to `select *`.

    Args:
        tables (List[Table]): List of Tables
        relationships (List[Ref]): List of Relationships between Tables

    Returns:
        List[Table]: Enriched tables

    """
    copied_tables = copy.deepcopy(tables)
    for relationship in relationships:
        for table in copied_tables:
            table_columns = [x.name.lower() for x in table.columns]
            if table.name == relationship.table_map[0] and relationship.column_map[0].lower() not in table_columns:
                table.columns.append(Column(name=relationship.column_map[0]))
            if table.name == relationship.table_map[1] and relationship.column_map[1].lower() not in table_columns:
                table.columns.append(Column(name=relationship.column_map[1]))
    return copied_tables


def get_table_from_metadata(model_metadata, exposures=None, **kwargs) -> Table:
    """
    Construct a single Table object (for Metadata).

    Args:
        model_metadata (dict): Metadata model node
        exposures (list, optional): List of parsed exposures. Defaults to [].
        **kwargs: Additional options including:
            entity_name_format (str): Format string for entity names
            omit_columns (bool): Whether to exclude columns from tables

    Returns:
        Table: Parsed table

    """
    if exposures is None:
        exposures = []
    node_name = model_metadata.get("node", {}).get("uniqueId")
    node_description = model_metadata.get("node", {}).get("description")
    node_database = model_metadata.get("node", {}).get("database").lower()
    node_schema = model_metadata.get("node", {}).get("schema").lower()
    node_label = model_metadata.get("node", {}).get("meta", {}).get("label")
    node_name_parts = node_name.split(".")
    table = Table(
        name=get_table_name(
            format=kwargs.get("entity_name_format"),
            **{
                "resource": node_name_parts[0],
                "package": node_name_parts[1],
                "model": node_name_parts[2],
                "database": node_database,
                "schema": model_metadata.get("node", {}).get("schema").lower(),
                "table": (
                    model_metadata.get("node", {}).get("alias") or model_metadata.get("node", {}).get("name")
                ).lower(),
            },
        ),
        node_name=node_name,
        raw_sql=None,
        database=node_database,
        schema=node_schema,
        columns=[],
        resource_type=node_name.split(".")[0],
        exposures=[x.get("exposure_name") for x in exposures if x.get("node_name") == node_name],
        description=node_description,
        label=node_label,
    )

    # columns
    table_catalog = model_metadata.get("node", {}).get("catalog", {})
    if table_catalog:
        for column in table_catalog.get("columns", []):
            table.columns.append(
                Column(
                    name=column.get("name", "").lower(),
                    data_type=column.get("type", "").lower(),
                    description=column.get("description", ""),
                )
            )

    if not table.columns:
        table.columns.append(Column())

    return table


def get_table(node_name: str, manifest_node, catalog_node=None, exposures=None, **kwargs) -> Table:
    """
    Construct a single Table object.

    Args:
        node_name (str): Node name
        manifest_node (dict): Manifest node
        catalog_node (dict, optional): Catalog node. Defaults to None.
        exposures (List, optional): List of table-exposure mapping. Defaults to [].
        **kwargs: Additional options including:
            entity_name_format (str): Format string for entity names
            omit_columns (bool): Whether to exclude columns from tables

    Returns:
        Table: Parsed table

    """
    if exposures is None:
        exposures = []
    node_name_parts = node_name.split(".")
    table = Table(
        name=get_table_name(
            format=kwargs.get("entity_name_format"),
            **{
                "resource": node_name_parts[0],
                "package": node_name_parts[1],
                "model": node_name_parts[2],
                "database": manifest_node.database.lower(),
                "schema": manifest_node.schema_.lower(),
                "table": (
                    manifest_node.identifier.lower()
                    if hasattr(manifest_node, "identifier")
                    else (manifest_node.alias.lower() if hasattr(manifest_node, "alias") else node_name)
                ),
            },
        ),
        node_name=node_name,
        raw_sql=get_compiled_sql(manifest_node),
        database=manifest_node.database.lower(),
        schema=manifest_node.schema_.lower(),
        columns=[],
        resource_type=node_name.split(".")[0],
        exposures=[x.get("exposure_name") for x in exposures if x.get("node_name") == node_name],
        description=manifest_node.description,
        label=manifest_node.meta.get("label"),
    )

    if catalog_node:
        for column, metadata in catalog_node.columns.items():
            table.columns.append(
                Column(
                    name=str(column).lower(),
                    data_type=str(metadata.type).lower(),
                    description=metadata.comment or "",
                )
            )

    for original_column_name, column_metadata in manifest_node.columns.items():
        column_name = original_column_name.strip('"')
        find_columns = [c for c in table.columns if c.name.lower() == column_name.lower()]
        if not find_columns:
            table.columns.append(
                Column(
                    name=column_name.lower(),
                    data_type=str(column_metadata.data_type or "unknown").lower(),
                    description=column_metadata.description or "",
                )
            )
        else:
            find_columns[0].description = find_columns[0].description or column_metadata.description or ""

    if not table.columns:
        table.columns.append(Column())

    return table


def get_compiled_sql(manifest_node):
    """
    Retrieve compiled SQL from manifest node.

    Args:
        manifest_node (dict): Manifest node

    Returns:
        str: Compiled SQL

    """
    if hasattr(manifest_node, "compiled_sql"):  # up to v6
        return manifest_node.compiled_sql

    if hasattr(manifest_node, "compiled_code"):  # from v7
        return manifest_node.compiled_code

    if hasattr(manifest_node, "columns"):  # nodes having no compiled but just list of columns
        return """select
            {columns}
        from {table}""".format(
            columns=",\n".join([f"{x}" for x in manifest_node.columns]),
            table=f"{manifest_node.database}.{manifest_node.schema}.undefined",
        )

    return manifest_node.raw_sql  # fallback to raw dbt code


def get_node_exposures_from_metadata(data=None, **kwargs):
    """
    Get the mapping of table name and exposure name (for Metadata).

    Args:
        data (list, optional): Metadata result list. Defaults to [].
        **kwargs: Additional options that might be passed from parent functions

    Returns:
        list: List of mapping dict {table_name:..., exposure_name=...}

    """
    if data is None:
        data = []
    exposures = []
    for data_item in data:
        for exposure in data_item.get("exposures", {}).get("edges", []):
            name = exposure.get("node", {}).get("name")
            parent_nodes = exposure.get("node", {}).get("parents")
            for node in parent_nodes:
                node_name = node.get("uniqueId", "")
                if node_name.split(".")[0] in kwargs.get("resource_type", []):
                    exposures.append(
                        {
                            "node_name": node_name,
                            "exposure_name": name,
                        }
                    )

    return exposures


def get_node_exposures(manifest: Manifest) -> list[dict[str, str]]:
    """
    Get the mapping of table name and exposure name.

    Args:
        manifest (dict): dbt manifest json

    Returns:
        list: List of mapping dict {table_name:..., exposure_name=...}

    """
    exposures = []

    if hasattr(manifest, "exposures"):
        for exposure_name, node in manifest.exposures.items():
            for node_name in node.depends_on.nodes:
                exposures.append(
                    {
                        "node_name": node_name,
                        "exposure_name": exposure_name.split(".")[-1],
                    }
                )

    return exposures


def get_table_name(format: str, **kwargs) -> str:
    """
    Get table name from the input format.

    Args:
        format (str): Table format string e.g. resource.package.model
        **kwargs: Dictionary containing values for each format part

    Returns:
        str: Qualified table name

    """
    return ".".join([kwargs.get(x.lower()) or "KEYNOTFOUND" for x in format.split(".")])


def get_test_nodes_by_rule_name(manifest: Manifest, rule_name: str) -> list:
    """
    Get manifest nodes given the algo rule name.

    Default algo rule name is `relationship`,
    see `get_algo_rule` function for more details.

    Args:
        rule_name (str): Rule name
        manifest (Manifest): Manifest data

    Returns:
        List: List of manifest nodes

    """
    return [
        x
        for x in manifest.nodes
        if (
            x.startswith("test")
            and rule_name in x.lower()
            and manifest.nodes[x].meta.get(TEST_META_IGNORE_IN_ERD, "0") == "0"
        )
    ]


def get_relationships_from_metadata(data=None, **kwargs) -> list[Ref]:
    """
    Extract relationships from Metadata result list on test relationship.

    Args:
        data (list, optional): Metadata result list. Defaults to [].
        **kwargs: Additional options including:
            rule_name (str): Custom rule name for the algorithm
            rule_prefix (str): Prefix for the rule

    Returns:
        list[Ref]: List of parsed relationship

    """
    if data is None:
        data = []
    refs = []
    rule = get_algo_rule(**kwargs)

    for data_item in data:
        for test in data_item.get("tests", {}).get("edges", []):
            test_id = test.get("node", {}).get("uniqueId", "")
            test_meta = test.get("node", {}).get("meta", {})
            if (
                test_id.startswith("test")
                and rule.get("name").lower() in test_id.lower()
                and test_meta is not None
                and test_meta.get(TEST_META_IGNORE_IN_ERD, "0") == "0"
            ):
                test_metadata_kwargs = test.get("node", {}).get("testMetadata", {}).get("kwargs", {})
                refs.append(
                    Ref(
                        name=test_id,
                        table_map=get_table_map_from_metadata(test_node=test, **kwargs),
                        column_map=[
                            (
                                test_metadata_kwargs.get(rule.get("c_to"))
                                or "_and_".join(test_metadata_kwargs.get(f"{rule.get('c_to')}s", "unknown"))
                            )
                            .replace('"', "")
                            .lower(),
                            (
                                test_metadata_kwargs.get("columnName")
                                or test_metadata_kwargs.get(rule.get("c_from"))
                                or "_and_".join(test_metadata_kwargs.get(f"{rule.get('c_from')}s", "unknown"))
                            )
                            .replace('"', "")
                            .lower(),
                        ],
                        type=get_relationship_type(test_meta.get(TEST_META_RELATIONSHIP_TYPE, "")),
                        relationship_label=test_meta.get("relationship_label"),
                    )
                )

    return get_unique_refs(refs=refs)


def get_relationships(manifest: Manifest, **kwargs) -> list[Ref]:
    """
    Extract relationships from dbt artifacts based on test relationship.

    Args:
        manifest (dict): Manifest json
        **kwargs: Additional options including:
            rule_name (str): Custom rule name for the algorithm
            rule_prefix (str): Prefix for the rule
            entity_name_format (str): Format for entity names

    Returns:
        List[Ref]: List of parsed relationship

    """
    rule = get_algo_rule(**kwargs)
    refs = [
        Ref(
            name=x,
            table_map=get_table_map(test_node=manifest.nodes[x], **kwargs),
            column_map=[
                str(
                    manifest.nodes[x].test_metadata.kwargs.get(rule.get("c_to"))
                    or "_and_".join(manifest.nodes[x].test_metadata.kwargs.get(f"{rule.get('c_to')}s", "unknown"))
                )
                .replace('"', "")
                .lower(),
                str(
                    manifest.nodes[x].test_metadata.kwargs.get("column_name")
                    or manifest.nodes[x].test_metadata.kwargs.get(rule.get("c_from"))
                    or "_and_".join(manifest.nodes[x].test_metadata.kwargs.get(f"{rule.get('c_from')}s", "unknown"))
                )
                .replace('"', "")
                .lower(),
            ],
            type=get_relationship_type(manifest.nodes[x].meta.get(TEST_META_RELATIONSHIP_TYPE, "")),
            relationship_label=manifest.nodes[x].meta.get("relationship_label"),
        )
        for x in get_test_nodes_by_rule_name(manifest=manifest, rule_name=rule.get("name").lower())
    ]

    return get_unique_refs(refs=refs)


def make_up_relationships(relationships: Optional[list[Ref]] = None, tables: Optional[list[Table]] = None) -> list[Ref]:
    """
    Filter Refs given by the parsed Tables & applied the entity name format.

    Args:
        relationships (List[Ref], optional): Parsed relationships. Defaults to [].
        tables (List[Table], optional): Parsed tables. Defaults to [].

    Returns:
        List[Ref]: Cooked relationships

    """
    if tables is None:
        tables = []
    if relationships is None:
        relationships = []
    node_names = [x.node_name for x in tables]
    relationships = [
        Ref(
            name=x.name,
            table_map=[
                next(t for t in tables if t.node_name == x.table_map[0]).name,
                next(t for t in tables if t.node_name == x.table_map[1]).name,
            ],
            column_map=x.column_map,
            type=x.type,
            relationship_label=x.relationship_label,
        )
        for x in relationships
        if x.table_map[0] in node_names and x.table_map[1] in node_names
    ]

    return relationships


def get_unique_refs(refs: Optional[list[Ref]] = None) -> list[Ref]:
    """
    Remove duplicates in the Relationship list.

    Args:
        refs (list[Ref], optional): List of parsed relationship. Defaults to [].

    Returns:
        list[Ref]: Distinct parsed relationship

    """
    if refs is None:
        refs = []
    if not refs:
        return []

    distinct_list = [refs[0]]
    for ref in refs:
        distinct_maps = [str((x.table_map, x.column_map)) for x in distinct_list]
        if str((ref.table_map, ref.column_map)) not in distinct_maps:
            distinct_list.append(ref)

    return distinct_list


def get_algo_rule(**kwargs) -> dict[str, str]:
    """
    Extract rule from the --algo option.

    Args:
        **kwargs: Additional options including:
            algo (str): Algorithm name and optional rules

    Details:
        Samples for algo parameter:
        - test_relationship
        - test_relationship:(name:relationship|c_from:column_name|c_to:field)
        - test_relationship:(name:foreign_key|c_from:fk_column_name|c_to:pk_column_name)
        - test_relationship:(
            name:foreign_key|
            c_from:fk_column_name|
            c_to:pk_column_name|
            t_to:pk_table_name
        )

    Returns:
        dict: Rule object (
            name [default 'relationship', use contains],
            c_from [default 'column_name'],
            c_to [default 'field'],
            t_to [default 'to']
        )

    """
    algo_parts = (kwargs.get("algo") or "").replace(" ", "").split(":", 1)
    rules, _ = (
        algo_parts[1] if len(algo_parts) > 1 else DEFAULT_ALGO_RULE,
        algo_parts[0],
    )
    rules = rules[1:-1]  # remove brackets
    rules = dict(arg.split(":") for arg in rules.split("|"))
    return rules


def get_table_map_from_metadata(test_node, **kwargs) -> list[str]:
    """
    Get the table map with order of [to, from] guaranteed.

    (for Metadata)

    Args:
        test_node (dict): Metadata test node
        **kwargs: Additional options passed from parent functions

    Raises:
        click.BadParameter: A Ref must have 2 parents

    Returns:
        list: [to model, from model]

    """
    rule = get_algo_rule(**kwargs)

    test_parents = []
    for parent in test_node.get("node", {}).get("parents", []):
        parent_id = parent.get("uniqueId", "")
        if parent_id.split(".")[0] in kwargs.get("resource_type", []):
            test_parents.append(parent_id)

    if len(test_parents) == 0:
        return ["", ""]  # return dummies - need to be excluded manually

    if len(test_parents) == 1:
        return [test_parents[0], test_parents[0]]  # self FK

    if len(test_parents) > MAX_TEST_PARENTS:
        logger.debug(f"Collected test parents: {test_parents}")
        raise click.BadParameter("Relationship test unexpectedly doesn't have >2 parents")

    test_metadata_to = (
        test_node.get("node", {}).get("testMetadata", {}).get("kwargs", {}).get(rule.get("t_to", "to"), "")
    )

    first_test_parent_parts = test_parents[0].split(".")
    first_test_parent_resource_type = "ref" if first_test_parent_parts[0] != "source" else first_test_parent_parts[0]
    to_model_possible_values = [
        f"{first_test_parent_resource_type}('{first_test_parent_parts[2]}', '{first_test_parent_parts[-1]}')",
        f"{first_test_parent_resource_type}('{first_test_parent_parts[-1]}')",
        f'{first_test_parent_resource_type}("{first_test_parent_parts[2]}", "{first_test_parent_parts[-1]}")',
        f'{first_test_parent_resource_type}("{first_test_parent_parts[-1]}")',
    ]
    if test_metadata_to in to_model_possible_values:
        return test_parents

    return list(reversed(test_parents))


def get_table_map(test_node, **kwargs) -> list[str]:
    """
    Get the table map with order of [to, from] guaranteed.

    Args:
        test_node (dict): Manifest Test node
        **kwargs: Additional options passed from parent functions

    Returns:
        list: [to model, from model]

    """
    map = test_node.depends_on.nodes or []

    # Recursive relation case
    # `from` and `to` will be identical and `test_node.depends_on.nodes` will contain only one element
    if len(map) == 1:
        return [map[0], map[0]]

    rule = get_algo_rule(**kwargs)
    to_model = str(test_node.test_metadata.kwargs.get(rule.get("t_to", "to"), {}))
    if f'("{map[1].split(".")[-1]}")'.lower() in to_model.replace("'", '"').lower():
        return [map[1], map[0]]

    return map


def get_relationship_type(meta: str) -> str:
    """
    Get short form of the relationship type configured in test meta.

    Args:
        meta (str): meta value

    Returns:
        str: |
            Short relationship type. Accepted values: '0n','01','11','nn','n1' and '1n'.
            And `1n` is default/fallback value

    """
    if meta.lower() == "zero-to-many":
        return "0n"
    if meta.lower() == "zero-to-one":
        return "01"
    if meta.lower() == "one-to-one":
        return "11"
    if meta.lower() == "many-to-many":
        return "nn"
    if meta.lower() == "one-to-many":
        return "1n"
    return "n1"  # "many-to-one"
