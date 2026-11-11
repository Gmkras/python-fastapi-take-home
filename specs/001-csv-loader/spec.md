# Feature Specification: Cargador del dataset Coffee Health

**Feature Branch**: `001-csv-loader`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "Cargador del dataset coffee health: importar data/coffee_health_raw.csv
(unos 10.000 registros exportados de otro sistema, no limpios) a la base de datos, dejando solo
registros válidos y normalizados, y explicando cada fila que no se cargó." (descripción completa,
con reglas y criterio de éxito, en el historial de `/speckit-specify`; su contenido está
recogido íntegro en esta spec).

## Clarifications

### Session 2026-11-11

- Q: Cuando un número viene escrito con un error evidente pero adivinable (`4O`, `thirty`,
  `seven`), ¿se rechaza o se corrige? → A: Se rechaza siempre (no se interpretan palabras ni se
  sustituyen letras por dígitos), y el registro de la fila no cargada guarda el porqué: columna,
  valor original y motivo "no numérico".
- Q: Cuando el mismo ID aparece varias veces con valores distintos, ¿qué versión queda en la
  base? → A: Gana la última aparición válida; las anteriores se registran como "duplicado en
  conflicto" (o "duplicado exacto" si son idénticas) con la línea que las reemplazó.
- Q: Si al recargar, un ID que ya está en la base llega en una fila inválida, ¿qué pasa con el
  registro existente? → A: Se conserva sin cambios; la fila nueva queda registrada como
  rechazada.
- Q: ¿El registro de filas no cargadas guarda solo la última carga de cada archivo o un
  historial de ejecuciones? → A: Solo la última carga de cada archivo; recargar reemplaza sus
  filas no cargadas. No hay historial de ejecuciones.
- Q: Cuando a una fila bien formada le falta un valor (campo vacío, `n/a`, `?`, `unknown`),
  ¿se rechaza o se carga vacío? → A: Depende de la columna. Obligatorias: ID, Country,
  Coffee_Intake y Caffeine_mg (si faltan, se rechaza la fila). Las otras 12 son opcionales: se
  cargan vacías. Los errores de estructura y los valores inválidos se siguen rechazando.

## User Scenarios & Testing *(mandatory)*

Actor principal: la persona que opera la carga (desarrolladora o revisora de la prueba). Actor
secundario: quien consulta después los datos y necesita confiar en ellos.

### User Story 1 - Cargar solo registros válidos y normalizados (Priority: P1)

La persona operadora ejecuta la carga sobre el archivo exportado. Al terminar, la base contiene un
registro por persona, con valores normalizados a su forma canónica, y ningún registro inválido.

**Why this priority**: Es el objetivo de la feature; sin esto no hay datos que servir ni que
explicar.

**Independent Test**: Ejecutar la carga sobre una base vacía con el archivo actual y comprobar que
la base contiene exactamente 9.984 registros, todos dentro de los dominios y rangos definidos.

**Acceptance Scenarios**:

1. **Given** una base vacía, **When** se carga el archivo actual, **Then** la base contiene 9.984
   registros con IDs únicos, de los cuales 9 tienen algún dato opcional vacío.
2. **Given** una fila con `' Germany  '`, `'SWEDEN'`, `'male'` o `'GOOD'`, **When** se carga,
   **Then** el registro queda con `Germany`, `Sweden`, `Male` o `Good` y la fila cuenta como
   corregida.
3. **Given** una fila con país `'usa'` o `'uk'`, **When** se carga, **Then** queda como `USA` o
   `UK` (no `Usa` ni `Uk`).
4. **Given** una fila con Smoking `'Y'` o Alcohol_Consumption `'false'`, **When** se carga,
   **Then** se guarda como verdadero o falso respectivamente y la fila cuenta como corregida.
5. **Given** una fila con todos sus campos entre comillas y valores correctos, **When** se carga,
   **Then** se guarda y no cuenta como corregida.
6. **Given** el archivo con BOM UTF-8 y finales de línea CRLF, **When** se carga, **Then** la
   cabecera se reconoce y el resultado es el mismo que con un archivo sin BOM y con finales LF.
7. **Given** una fila bien formada con Age vacío (línea 1154) o BMI `'n/a'` (línea 7902),
   **When** se carga, **Then** el registro se guarda con ese dato vacío y cuenta como "cargada con
   datos faltantes".
8. **Given** una fila con Country o Coffee_Intake vacío, **When** se carga, **Then** se rechaza
   con motivo "falta valor obligatorio" y la columna afectada.

---

### User Story 2 - Explicar cada fila no cargada (Priority: P1)

Después de la carga, la persona operadora puede consultar, para cada fila que no entró, su número
de línea en el archivo, el motivo, la columna afectada, el valor original y la fila original
completa. Ve también un resumen con los conteos de la carga.

**Why this priority**: La prueba exige poder explicar cada fila no cargada y que los conteos
concilien; sin esto la carga no es defendible.

**Independent Test**: Cargar el archivo actual y comprobar que hay exactamente 28 filas no
cargadas registradas (16 rechazadas + 12 duplicadas), cada una con línea y motivo, y que el
resumen cuadra.

**Acceptance Scenarios**:

1. **Given** el archivo actual, **When** termina la carga, **Then** el resumen muestra: 10.012
   filas de datos leídas, 3 en blanco, 9.996 válidas, 56 corregidas, 9 cargadas con datos
   faltantes, 16 rechazadas, 12 duplicadas (8 exactas y 4 en conflicto) y 9.984 registros en la
   base.
2. **Given** el archivo actual, **When** termina la carga, **Then** los rechazos por columna son:
   3 por número de columnas, 5 por Age, 3 por Sleep_Hours, 2 por Heart_Rate, 2 por Smoking y 1
   por BMI.
3. **Given** una fila con Age `'thirty'` en la línea 816, **When** se carga, **Then** queda
   registrada como rechazada con línea 816, columna Age, valor `thirty` y motivo "no numérico".
4. **Given** una fila con 17 campos en la línea 2659, **When** se carga, **Then** queda registrada
   como rechazada por número de columnas, con la fila original completa.
5. **Given** cualquier fila no cargada, **When** se consultan los rechazos de la carga, **Then**
   aparece exactamente una vez.
6. **Given** que la suma de rechazadas + duplicadas + cargadas no coincide con las filas leídas, o
   que el total de la base no coincide con el resumen, **When** termina la carga, **Then** el
   proceso termina con error y no confirma cambios.

---

### User Story 3 - Resolver IDs repetidos de forma determinista (Priority: P2)

Cuando el mismo ID aparece varias veces con filas válidas, la carga conserva una sola versión
siguiendo una regla fija y documentada, e informa qué filas descartó y por qué.

**Why this priority**: Afecta a 12 filas y es el caso ambiguo que la prueba pide decidir y
documentar; la carga funciona sin él, pero con datos ambiguos.

**Independent Test**: Cargar un archivo pequeño con un ID repetido con valores distintos y
comprobar que queda la última versión y la anterior aparece como duplicado en conflicto.

**Acceptance Scenarios**:

1. **Given** el ID 3338 en las líneas 3339 (Age 33) y 3516 (Age 26), **When** se carga, **Then**
   queda el registro de la línea 3516 y la línea 3339 se registra como "duplicado en conflicto"
   reemplazada por la línea 3516.
2. **Given** un ID repetido con filas idénticas tras normalizar, **When** se carga, **Then** queda
   un solo registro y la aparición anterior se registra como "duplicado exacto".
3. **Given** un ID cuya última aparición es inválida y una anterior es válida, **When** se carga,
   **Then** se carga la aparición válida y la inválida cuenta como rechazada, no como duplicada.

---

### User Story 4 - Repetir la carga sin efectos secundarios (Priority: P2)

La persona operadora puede ejecutar la carga tantas veces como quiera: con el mismo archivo no
cambia nada; con un archivo actualizado, se actualizan los registros por ID. Si la carga falla a
mitad, la base queda como estaba.

**Why this priority**: Requisito explícito de la prueba; permite recargar con confianza.

**Independent Test**: Ejecutar la carga dos veces seguidas y comprobar que la segunda reporta 0
insertadas y 0 actualizadas y que el número de registros y de rechazos no cambia.

**Acceptance Scenarios**:

1. **Given** el archivo ya cargado, **When** se carga otra vez, **Then** el resumen muestra 0
   insertadas, 0 actualizadas y 9.984 sin cambios, y la base sigue con 9.984 registros y 28 filas
   no cargadas registradas.
2. **Given** el archivo ya cargado y una copia en la que cambia el Heart_Rate de un ID, **When** se
   carga la copia, **Then** se actualiza 1 registro y no se inserta ninguno.
3. **Given** una carga que falla antes de terminar, **When** se inspecciona la base, **Then**
   registros y rechazos están exactamente como antes de empezar.
4. **Given** rechazos registrados de un archivo A, **When** se carga un archivo B distinto,
   **Then** los rechazos de A se conservan.
5. **Given** un ID ya cargado, **When** se recarga un archivo donde la única fila de ese ID es
   inválida, **Then** el registro existente no cambia y la fila queda registrada como rechazada.

---

### User Story 5 - Procesar archivos mucho más grandes (Priority: P3)

La misma carga funciona con un archivo de decenas de millones de filas que no cabe en memoria,
sin cambiar el procedimiento.

**Why this priority**: La prueba pide diseñar pensando en escala; no cambia el resultado con el
archivo actual.

**Independent Test**: Cargar un archivo sintético de al menos 1 millón de filas y comprobar que el
consumo de memoria no crece con el tamaño del archivo y que los conteos concilian.

**Acceptance Scenarios**:

1. **Given** un archivo de 1 millón de filas, **When** se carga, **Then** termina con los conteos
   conciliados y su consumo de memoria es comparable al de cargar el archivo de 10.000 filas.

---

### Edge Cases

- Líneas en blanco (5953, 10015, 10016 y el salto final): se omiten y se cuentan aparte; no son
  rechazos ni se registran.
- Cabecera ausente, con columnas de más o de menos, o en otro orden: el proceso se detiene antes
  de leer datos con un error que indica qué columnas esperaba y cuáles encontró.
- Archivo inexistente o ilegible: error claro y la base no cambia.
- Fila con varias columnas inválidas: se rechaza una sola vez; el motivo principal es la primera
  columna inválida en el orden de la cabecera y el detalle nombra todas.
- Fila que es corregible y además inválida: cuenta solo como rechazada.
- Valores con letras que parecen números (`4O`): se rechazan, no se adivinan.
- Números con signo, notación científica, separador de miles, coma decimal, `NaN` o `Infinity`:
  se rechazan como no numéricos.
- Enteros con decimales (`40.0` en Age): se rechazan como no enteros.
- Decimales con más de una cifra (`3.14`): se redondean a una cifra (`3.1`, mitad hacia arriba)
  y la fila cuenta como corregida; si tras redondear queda fuera de rango, se rechaza.
- `None` en Health_Issues es un valor válido del dominio, no un faltante.
- Campo vacío o marcador de faltante (`''`, `n/a`, `?`, `unknown`) en una columna opcional: la
  fila se carga con ese dato vacío; en una columna obligatoria, se rechaza.
- Valor inválido en una columna opcional (`thirty`, `-3`, `350`): se rechaza igual; opcional
  solo significa que puede faltar, no que acepte cualquier cosa.
- Dos apariciones del mismo ID que solo difieren en que una tiene un dato vacío: son un
  "duplicado en conflicto" y gana la última, aunque sea la que tiene el dato vacío.
- País con mayúsculas o espacios irregulares que corresponde a un país conocido: se corrige; país
  no reconocido: se rechaza.
- ID cero, negativo o no entero: se rechaza.
- La misma fila repetida tres o más veces: queda la última; todas las anteriores se registran
  como duplicadas.
- Ejecución sin conexión a la base: error claro y sin cambios.

## Requirements *(mandatory)*

### Functional Requirements

**Lectura y estructura**

- **FR-001**: El sistema DEBE leer archivos delimitados por comas con o sin BOM UTF-8, con finales
  de línea CRLF o LF, y con campos entre comillas o sin ellas.
- **FR-002**: El sistema DEBE verificar que la cabecera contiene exactamente las 16 columnas
  esperadas, en este orden: ID, Age, Gender, Country, Coffee_Intake, Caffeine_mg, Sleep_Hours,
  Sleep_Quality, BMI, Heart_Rate, Stress_Level, Physical_Activity_Hours, Health_Issues,
  Occupation, Smoking, Alcohol_Consumption. Si no coincide, DEBE detenerse antes de procesar datos
  con un error que muestre lo esperado y lo encontrado.
- **FR-003**: El sistema DEBE omitir las líneas en blanco y contarlas aparte.
- **FR-004**: El sistema DEBE identificar cada fila por su número de línea en el archivo,
  contando la cabecera como línea 1.
- **FR-005**: El sistema DEBE rechazar las filas cuyo número de campos no sea 16.

**Normalización (la fila se carga y cuenta como corregida)**

- **FR-006**: El sistema DEBE eliminar espacios y tabulaciones al inicio y al final de cada valor.
- **FR-007**: El sistema DEBE llevar los valores categóricos a su forma canónica sin distinguir
  mayúsculas: Gender (Male, Female, Other), Sleep_Quality (Poor, Fair, Good, Excellent),
  Stress_Level (Low, Medium, High), Health_Issues (None, Mild, Moderate, Severe), Occupation
  (Office, Healthcare, Service, Student, Other).
- **FR-008**: El sistema DEBE llevar el país a su forma canónica según un catálogo de países
  conocidos, sin distinguir mayúsculas y respetando las siglas (`USA`, `UK`). El catálogo inicial
  son los 20 países presentes en el archivo: Australia, Belgium, Brazil, Canada, China, Finland,
  France, Germany, India, Italy, Japan, Mexico, Netherlands, Norway, South Korea, Spain, Sweden,
  Switzerland, UK, USA.
- **FR-009**: El sistema DEBE aceptar como booleanos `0` y `1`, y además convertir sin distinguir
  mayúsculas `y`, `yes`, `true` a verdadero y `n`, `no`, `false` a falso, contando la fila como
  corregida en este segundo caso.
- **FR-010**: El sistema DEBE redondear a un decimal (mitad hacia arriba) los valores decimales
  con más de una cifra decimal.
- **FR-011**: Una fila cuenta como "corregida" si es válida y al menos uno de sus valores cambió
  por FR-006 a FR-010. Las comillas del formato no cuentan como corrección.

**Validación (la fila se rechaza)**

- **FR-012**: El sistema DEBE tratar como valor faltante un campo vacío o un marcador de
  faltante (`n/a`, `na`, `?`, `unknown`, `null`, sin distinguir mayúsculas; `None` en
  Health_Issues es un valor válido). Si falta una columna obligatoria (ID, Country,
  Coffee_Intake, Caffeine_mg), la fila se rechaza con motivo "falta valor obligatorio". Si falta
  una columna opcional (las otras 12), la fila se carga con ese valor vacío.
- **FR-012a**: Un valor faltante NO es un valor inválido: `thirty` o `-3` se rechazan por FR-013
  y FR-014 aunque la columna sea opcional.
- **FR-013**: El sistema DEBE rechazar los enteros (ID, Age, Heart_Rate) que no estén formados solo
  por dígitos, y los decimales (Coffee_Intake, Caffeine_mg, Sleep_Hours, BMI,
  Physical_Activity_Hours) que no sean dígitos con un punto decimal opcional. No se interpretan
  palabras (`thirty`, `seven`) ni se sustituyen letras por dígitos (`4O`); la fila se registra
  como no cargada con la columna, el valor original y el motivo "no numérico".
- **FR-014**: El sistema DEBE rechazar los valores fuera de estos rangos plausibles (inclusive):
  ID ≥ 1; Age 18–120; Coffee_Intake 0–20 tazas/día; Caffeine_mg 0–2000 mg/día; Sleep_Hours 1–16
  h/noche; BMI 10–80; Heart_Rate 30–200 ppm; Physical_Activity_Hours 0–80 h/semana.
- **FR-015**: El sistema DEBE rechazar categorías y países que no estén en sus catálogos tras
  normalizar.
- **FR-016**: El sistema DEBE rechazar booleanos que no estén en FR-009.
- **FR-017**: Una fila inválida NO DEBE detener la carga.

**Duplicados**

- **FR-018**: Entre filas válidas con el mismo ID, el sistema DEBE conservar la que aparece en la
  línea más alta del archivo (gana la última aparición válida). Se descartaron "gana la primera"
  y "no cargar ninguna versión en conflicto".
- **FR-019**: Cada fila válida descartada por FR-018 DEBE registrarse como no cargada con motivo
  "duplicado exacto" (idéntica a la conservada tras normalizar) o "duplicado en conflicto"
  (difiere en algún valor), indicando la línea que la reemplazó.

**Registro de filas no cargadas**

- **FR-020**: Toda fila no cargada (rechazada o duplicada) DEBE quedar registrada de forma
  consultable con: archivo de origen, número de línea, motivo, columna afectada (si aplica),
  valor original (si aplica), detalle y la fila original completa.
- **FR-021**: Volver a cargar el mismo archivo DEBE reemplazar sus filas no cargadas registradas,
  sin duplicarlas; las de otros archivos NO DEBEN cambiar. Solo se conserva la última carga de
  cada archivo; no se guarda historial de ejecuciones.

**Escritura, idempotencia y atomicidad**

- **FR-022**: El sistema DEBE insertar los IDs nuevos y actualizar los existentes cuyos valores
  cambiaron; los que no cambiaron NO DEBEN modificarse. Una fila rechazada nunca modifica ni
  borra un registro existente con el mismo ID.
- **FR-023**: Toda la carga DEBE confirmarse como una unidad: si falla en cualquier punto, ni los
  registros ni las filas no cargadas registradas cambian.
- **FR-024**: La base DEBE impedir por sí misma guardar valores fuera de los dominios y rangos de
  FR-007, FR-008 (salvo el catálogo de países), FR-014 y FR-016, y valores vacíos en las
  columnas obligatorias, como segunda barrera.

**Resumen y conciliación**

- **FR-025**: Al terminar, el sistema DEBE mostrar: filas de datos leídas, en blanco, válidas,
  corregidas, cargadas con datos faltantes, rechazadas (total y por motivo), duplicadas
  (exactas y en conflicto), insertadas,
  actualizadas, sin cambios y total de registros en la base.
- **FR-026**: El sistema DEBE verificar que leídas = rechazadas + duplicadas + cargadas y que el
  número de filas no cargadas registradas para el archivo coincide con rechazadas + duplicadas.
  Si no se cumple, DEBE terminar con error sin confirmar cambios.
- **FR-027**: El proceso DEBE terminar con código de salida distinto de cero ante cualquier error
  fatal (cabecera, archivo, conexión, conciliación) y con cero en caso de éxito, aunque haya
  filas rechazadas.

**Escala**

- **FR-028**: El consumo de memoria del proceso NO DEBE depender del número de filas del archivo.

### Key Entities

- **Registro de salud**: una persona del dataset, identificada por ID. Atributos obligatorios:
  ID, país, tazas de café al día y cafeína diaria. Atributos opcionales (pueden estar vacíos):
  edad, género, horas de sueño, calidad de sueño, BMI, frecuencia cardiaca en reposo, nivel de
  estrés, horas de actividad física semanal, problemas de salud, ocupación, si fuma y si consume
  alcohol. Además, cuándo se cargó o actualizó por última vez.
- **Fila no cargada**: una línea del archivo de origen que no llegó a ser registro. Atributos:
  archivo de origen, número de línea (únicos en conjunto), motivo, columna, valor original,
  detalle (incluye la línea que la reemplazó si es duplicado), fila original y momento de la
  carga.
- **Resumen de carga**: los conteos de una ejecución (FR-025); se muestra al terminar, no se
  guarda (sin historial de ejecuciones).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Con el archivo actual, la carga deja exactamente 9.984 registros (9 con algún dato
  opcional vacío) y 28 filas no cargadas registradas (16 rechazadas + 12 duplicadas), y
  10.012 = 16 + 12 + 9.984.
- **SC-002**: El 100 % de las filas no cargadas tiene número de línea y motivo, y cada una aparece
  una sola vez.
- **SC-003**: El 100 % de los registros cargados cumple los dominios y rangos definidos; ningún
  valor categórico queda fuera de su forma canónica.
- **SC-004**: Una segunda carga del mismo archivo produce 0 inserciones y 0 actualizaciones y deja
  los mismos conteos.
- **SC-005**: El archivo actual se carga completo en menos de 10 segundos en un equipo de
  desarrollo.
- **SC-006**: Un archivo de 1 millón de filas se carga sin que la memoria del proceso supere el
  doble de la usada con el archivo actual, a un ritmo de al menos 20.000 filas por segundo.
- **SC-007**: Cualquier fila no cargada puede explicarse (motivo y valor) con una sola consulta a
  los datos registrados, sin abrir el archivo original.

## Assumptions

- El archivo es UTF-8 (con o sin BOM) y usa coma como separador y punto como separador decimal.
- Solo 4 columnas son obligatorias (ID, Country, Coffee_Intake, Caffeine_mg): identifican el
  registro y son el tema del dataset. Las demás pueden quedar vacías; nunca se imputan. Los
  filtros por rango sobre una columna opcional no incluyen los registros con ese valor vacío.
- Los rangos plausibles de FR-014 son deliberadamente holgados frente a los datos observados
  (Age 18–80, café 0–8,2, cafeína 0–780,3, sueño 3–10, BMI 15–38,2, pulso 50–109, actividad
  0–15): solo excluyen lo físicamente imposible o claramente erróneo.
- Age mínima 18 porque el dataset declara adultos.
- No se valida la coherencia entre columnas (por ejemplo, cafeína frente a tazas): la relación
  observada varía de 49 a 140 mg por taza y no hay un umbral defendible.
- "Gana la última aparición" asume que el archivo está en orden cronológico de exportación; es la
  misma regla que aplica al recargar un archivo más nuevo.
- Un ID identifica a la misma persona entre archivos: cargar otro archivo con el mismo ID la
  actualiza.
- El archivo de origen se identifica por su nombre de archivo para reemplazar sus filas no
  cargadas al recargarlo.
- Una sola carga a la vez; las cargas concurrentes quedan fuera de alcance.
- Fuera de alcance: la API de consulta (feature 002), validaciones cruzadas entre columnas,
  imputación de faltantes, borrar registros que desaparezcan de un archivo nuevo.
