"""Schemas de los endpoints orientados a usuario (resolución por URL)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Nombre interno de columna: mismo patrón anti-inyección que los campos de filtro.
_ColumnName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+$")]

# Operadores admitidos en un filtro de búsqueda. Enum CERRADO a propósito: es la
# segunda mitad de la garantía anti-inyección (la primera es el patrón de
# `field`), de modo que ningún token del caller llega a la expresión OData.
_SearchOp = Literal["eq", "in", "ge", "gt", "le", "lt"]

# Máximo de valores de un `op: "in"`. Acota la longitud del $filter generado.
_IN_MAX_VALUES = 20

# Presupuesto total de cláusulas generadas tras expandir los `in` de todos los
# filtros. Margen de seguridad frente al límite de longitud de URL de Graph (no
# caracterizado por el proyecto), no un umbral medido.
_MAX_GENERATED_CLAUSES = 100


def _json_type_family(value: "str | bool | int | float") -> str:
    """Familia de tipo JSON del valor, para exigir arrays homogéneos en ``in``.

    ``bool`` se comprueba antes que ``int`` porque es subclase suya: ``[true, 1]``
    es un array mixto, no uno válido. ``int`` y ``float`` comparten familia —
    JSON tiene un único tipo numérico y las columnas numéricas aceptan ambos.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


class ListItemByUrlRequest(BaseModel):
    sharepoint_url: str = Field(
        ...,
        description=(
            "URL de la lista tal como aparece en el navegador, p. ej. "
            "https://host.sharepoint.com/sitio/Lists/MiLista/AllItems.aspx"
        ),
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Campos del nuevo ítem. Las claves deben ser los nombres internos "
            "(internal name) de las columnas de la lista."
        ),
    )


class ListItemByUrlResponse(BaseModel):
    status: str = "created"
    id: str
    webUrl: str | None = None
    site_id: str
    list_id: str


class FilterBy(BaseModel):
    field: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_]+$",
        description=(
            "Nombre interno (internal name) de la columna que identifica de forma "
            "única el registro, p. ej. '_x006c_dq4'. Solo letras, dígitos y '_'. "
            "Con 'ID' (sin distinguir mayúsculas) el ítem se direcciona "
            "directamente por su identificador en vez de buscarse."
        ),
    )
    value: str = Field(
        ...,
        description=(
            "Valor exacto que debe tener `field` en el registro a actualizar. "
            "Si `field` es 'ID', debe ser numérico (p. ej. '1087')."
        ),
    )


class ListItemUpdateByUrlRequest(BaseModel):
    sharepoint_url: str = Field(
        ...,
        description=(
            "URL de la lista tal como aparece en el navegador, p. ej. "
            "https://host.sharepoint.com/sitio/Lists/MiLista/AllItems.aspx"
        ),
    )
    filter_by: FilterBy = Field(
        ...,
        description=(
            "Campo único y valor con los que se localiza el registro a actualizar. "
            "Debe identificar un único ítem (si coincide más de uno se devuelve 409)."
        ),
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Campos a actualizar. Las claves deben ser los nombres internos "
            "(internal name) de las columnas de la lista."
        ),
    )


class ListItemUpdateByUrlResponse(BaseModel):
    status: str = "updated"
    id: str
    webUrl: str | None = None
    site_id: str
    list_id: str


class ExplicitPeriod(BaseModel):
    """Rango de fechas explícito para acotar el periodo del upsert.

    Ambos extremos son fechas ``YYYY-MM-DD``; ``to`` es **inclusive** (cubre todo
    ese día). Los límites se interpretan en la zona horaria del tenant.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(
        ...,
        alias="from",
        description="Fecha inicial inclusive (YYYY-MM-DD), en la zona del tenant.",
    )
    to: str = Field(
        ...,
        description="Fecha final inclusive (YYYY-MM-DD), en la zona del tenant.",
    )


class UpsertMatch(BaseModel):
    """Criterio de coincidencia del upsert: clave y, opcionalmente, periodo."""

    key_field: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_]+$",
        description=(
            "Nombre interno de la columna clave (variable por lista). Solo letras, "
            "dígitos y '_'."
        ),
    )
    key_value: str = Field(
        ..., description="Valor exacto que debe tener la columna clave."
    )
    date_field: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_]+$",
        description=(
            "Nombre interno de la columna de fecha que acota el periodo (p. ej. "
            "'Created', 'Modified' o una columna propia). Opcional."
        ),
    )
    period: str | ExplicitPeriod | None = Field(
        default=None,
        description=(
            "Alcance temporal. Atajo con nombre ('current_day', 'current_week', "
            "'current_month', 'current_year') o rango explícito {'from','to'}. "
            "Requiere `date_field`. Si se omite, la coincidencia es solo por clave."
        ),
    )

    @model_validator(mode="after")
    def _period_requires_date_field(self) -> "UpsertMatch":
        if self.period is not None and not self.date_field:
            raise ValueError(
                "Para acotar por periodo debe indicarse también `date_field`."
            )
        return self


class UpsertListItemRequest(BaseModel):
    sharepoint_url: str = Field(
        ...,
        description=(
            "URL de la lista tal como aparece en el navegador, p. ej. "
            "https://host.sharepoint.com/sitio/Lists/MiLista/AllItems.aspx"
        ),
    )
    match: UpsertMatch = Field(
        ..., description="Criterio para localizar el registro existente (clave + periodo)."
    )
    data: dict[str, Any] = Field(
        ...,
        description=(
            "Campos a escribir (crear o actualizar). Las claves deben ser los "
            "nombres internos de las columnas de la lista."
        ),
    )


class UpsertListItemResponse(BaseModel):
    result: Literal["created", "updated"]
    id: str
    webUrl: str | None = None
    site_id: str
    list_id: str
    matched: int = Field(
        default=0,
        description="Número de registros que coincidieron con el criterio.",
    )


class SearchFilter(BaseModel):
    """Una condición ``campo <op> valor`` de la búsqueda de ítems.

    ``op`` es un enum cerrado: el operador que llega al constructor de la
    expresión OData solo puede ser uno de seis tokens conocidos, así que ningún
    texto del caller puede colarse en el ``$filter``. Junto con el patrón
    anti-inyección de ``field``, todo lo que se interpola salvo el literal
    proviene de un alfabeto finito.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_]+$",
        description=(
            "Nombre interno (internal name) de la columna a filtrar. Solo letras, "
            "dígitos y '_'. Las columnas lookup se filtran con su campo "
            "'{Columna}LookupId' (valor entero)."
        ),
    )
    op: _SearchOp = Field(
        default="eq",
        description=(
            "Operador de comparación. 'eq' (por defecto): igualdad. 'in': "
            "pertenencia a una lista de valores — requiere `value` como array de "
            "1–20 elementos del mismo tipo y se expande a un grupo OR entre "
            "paréntesis. 'ge'/'gt'/'le'/'lt': comparaciones de orden, admitidas "
            "solo contra columnas numéricas, de moneda, de fecha/hora y "
            "'{Columna}LookupId' (contra texto, choice o Sí/No → 400). 'in' no se "
            "admite contra columnas Sí/No."
        ),
    )
    value: "str | bool | int | float | list[str | bool | int | float]" = Field(
        ...,
        description=(
            "Valor a comparar, en su tipo JSON natural: string, boolean "
            "(true/false, sin comillas), entero o decimal. Con `op: \"in\"` es un "
            "array de 1 a 20 valores del mismo tipo; con cualquier otro operador "
            "debe ser un escalar. El conector traduce el valor al literal OData "
            "correcto; el tipo debe corresponder al de la columna (se valida "
            "contra el esquema real de la lista). Las columnas de fecha/hora "
            "esperan un instante ISO-8601 en UTC, p. ej. '2026-08-18T00:00:00Z'."
        ),
    )

    @model_validator(mode="after")
    def _op_and_value_shape_agree(self) -> "SearchFilter":
        """Exige que la forma de ``value`` concuerde con ``op`` (422).

        Frontera 422/400 del endpoint: aquí se rechaza lo que puede juzgarse solo
        con el body; que la columna real admita el operador y el tipo se valida
        después, en el servicio, contra el esquema de la lista.
        """
        if self.op == "in":
            if not isinstance(self.value, list):
                raise ValueError(
                    '`op: "in"` requiere que `value` sea un array de valores.'
                )
            if not 1 <= len(self.value) <= _IN_MAX_VALUES:
                raise ValueError(
                    f'`op: "in"` admite entre 1 y {_IN_MAX_VALUES} valores; se '
                    f"recibieron {len(self.value)}."
                )
            families = {_json_type_family(v) for v in self.value}
            if len(families) > 1:
                raise ValueError(
                    'Los valores de `op: "in"` deben ser todos del mismo tipo '
                    f"JSON; se mezclaron: {', '.join(sorted(families))}."
                )
        elif isinstance(self.value, list):
            raise ValueError(
                '`value` solo puede ser un array con `op: "in"`; con '
                f'`op: "{self.op}"` debe ser un escalar.'
            )
        return self

    @property
    def clause_count(self) -> int:
        """Cláusulas OData que genera este filtro (un ``in`` genera una por valor)."""
        return len(self.value) if isinstance(self.value, list) else 1


class OrderBy(BaseModel):
    """Ordenación opcional de la búsqueda (``$orderby`` de Graph)."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_]+$",
        description=(
            "Nombre interno de la columna por la que ordenar. En listas grandes "
            "(por encima del umbral de vista de SharePoint) Graph solo permite "
            "ordenar por columnas indexadas (p. ej. 'Created', 'Modified', 'ID')."
        ),
    )
    direction: Literal["asc", "desc"] = Field(
        default="asc",
        description="Sentido de la ordenación: 'asc' (por defecto) o 'desc'.",
    )


class ListItemsSearchByUrlRequest(BaseModel):
    """Body de ``POST /v1/sharepoint/list/items:search``.

    ``extra="forbid"``: cualquier campo desconocido (p. ej. un ``filter_by`` de
    otros endpoints) se rechaza con 422 en lugar de ignorarse en silencio.
    """

    model_config = ConfigDict(extra="forbid")

    sharepoint_url: str = Field(
        ...,
        description=(
            "URL de la lista tal como aparece en el navegador, p. ej. "
            "https://host.sharepoint.com/sitio/Lists/MiLista/AllItems.aspx"
        ),
    )
    filters: list[SearchFilter] | None = Field(
        default=None,
        max_length=15,
        description=(
            "Hasta 15 condiciones combinadas con AND. Omitido o vacío → se "
            "devuelven los primeros `top` ítems sin filtrar. El OR solo existe "
            'dentro de una condición `op: "in"`; entre condiciones el enlace es '
            "siempre AND. Tras expandir los `in`, el total de cláusulas "
            "generadas no puede pasar de 100."
        ),
    )
    order_by: OrderBy | None = Field(
        default=None,
        description="Ordenación opcional del resultado.",
    )
    select: list[_ColumnName] | None = Field(
        default=None,
        max_length=50,
        description=(
            "Hasta 50 nombres internos de columna a devolver en `fields` de cada "
            "ítem (proyección). Omitido o vacío → todos los campos. Cada nombre "
            "se valida contra el esquema real de la lista. Las columnas lookup "
            "admiten ambas formas: el nombre base (valor visible) y "
            "'{Columna}LookupId' (ID numérico)."
        ),
    )
    top: int = Field(
        default=100,
        ge=1,
        le=5000,
        description=(
            "Número máximo de ítems a devolver (1–5000). El conector sigue la "
            "paginación de Graph hasta reunirlos y trunca el excedente."
        ),
    )

    @model_validator(mode="after")
    def _clause_budget_not_exceeded(self) -> "ListItemsSearchByUrlRequest":
        """Acota las cláusulas generadas tras expandir los ``in`` (422).

        El tope de 15 filtros no basta: 15 filtros ``in`` de 20 valores generarían
        300 cláusulas y un ``$filter`` que Graph podría rechazar por longitud de
        URL. El presupuesto se comprueba aquí, sobre el body, antes de resolver la
        lista.
        """
        generated = sum(f.clause_count for f in (self.filters or []))
        if generated > _MAX_GENERATED_CLAUSES:
            raise ValueError(
                f"Los filtros generan {generated} cláusulas OData y el máximo es "
                f"{_MAX_GENERATED_CLAUSES}. Reduce el número de filtros o de "
                'valores en los `op: "in"`.'
            )
        return self


class ListItemsSearchByUrlItem(BaseModel):
    id: str
    webUrl: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class ListItemsSearchByUrlResponse(BaseModel):
    total: int = Field(
        description="Número de ítems devueltos (tras el truncado por `top`)."
    )
    items: list[ListItemsSearchByUrlItem]
    has_more: bool = Field(
        default=False,
        description=(
            "true si el corte por `top` dejó fuera filas que también coincidían."
        ),
    )
    site_id: str
    list_id: str


class UploadByUrlResponse(BaseModel):
    status: str = "uploaded"
    id: str
    name: str
    size: int | None = None
    webUrl: str
    site_id: str
    drive_id: str
    folder: str
