# Feature Specification: API de consulta de registros Coffee Health

**Feature Branch**: `002-records-api`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "API de consulta de registros coffee health: que un cliente pueda
consultar por HTTP los registros ya cargados, filtrarlos y recorrerlos por páginas, usando la
documentación interactiva como única interfaz." (descripción completa, con historias por
prioridad, reglas de error, escala y fuera de alcance, en el historial de `/speckit-specify`; su
contenido está recogido íntegro en esta spec).

## Clarifications

### Session 2026-11-11

- Q: ¿Cómo trata la API un registro al que le falta un dato (12 columnas pueden quedar vacías
  desde la feature 001)? → A: El campo llega como nulo; un filtro sobre esa columna no incluye el
  registro; en el resumen forma un grupo "sin dato" y no cuenta en los promedios, de modo que los
  conteos del resumen suman siempre el total. No se añade un filtro para pedir "sin dato".
- Q: Con millones de filas, ¿qué consultas del listado deben responder igual de rápido en una
  página profunda que en la primera? → A: El listado sin filtros y el filtrado por país. Los
  demás filtros funcionan, pero su rapidez depende de cuántos registros coinciden; se documenta
  como compromiso. Se descartó garantizarlo para cada filtro por el costo de los índices en la
  carga.
- Q: ¿Los filtros de género, calidad de sueño y nivel de estrés aceptan el valor escrito de otra
  forma (`female`, `GOOD`) o solo la forma exacta? → A: No distinguen mayúsculas ni espacios
  sobrantes, con la misma regla que el país: `female` y `FEMALE` equivalen a `Female`. Un valor
  que tras normalizar no está en el catálogo sigue siendo un error de validación.
- Q: ¿Qué campos devuelve el listado de filas no cargadas? → A: Archivo, línea, motivo, columna,
  valor y detalle. El detalle es lo único que dice qué línea reemplazó a un duplicado. La fila
  original completa no se incluye.
- Q: ¿Cuánto puede esperar como máximo una petición antes de recibir el error temporal, por base
  saturada o por una consulta que tarda demasiado? → A: 5 segundos por defecto, configurable.
  Aplica por separado a la espera por la base y a la ejecución de la consulta.

## User Scenarios & Testing *(mandatory)*

Actor principal: el cliente de la API, una persona desarrolladora o revisora que la usa desde la
documentación interactiva del servicio. Actor secundario: quien operó la carga y quiere ver la
calidad del dato sin entrar a la base.

Los valores de referencia de los escenarios salen de los datos cargados por la feature 001 con el
archivo actual: 9.984 registros con IDs entre 1 y 10.000.

### User Story 1 - Listar registros por páginas (Priority: P1)

El cliente pide registros y recibe una página acotada, en orden estable por ID, junto con un
cursor para pedir la siguiente. Repitiendo la petición con el cursor recorre todos los registros
sin recibir nunca la tabla completa de una vez.

**Why this priority**: Es la funcionalidad mínima de la feature; sin listado no hay nada que
filtrar ni resumir. Devolver todo de una vez deja de ser viable en cuanto la tabla crece.

**Independent Test**: Recorrer todas las páginas con un tamaño de página dado y comprobar que se
reciben exactamente 9.984 registros, cada uno una sola vez y en orden ascendente de ID.

**Acceptance Scenarios**:

1. **Given** los datos cargados, **When** el cliente pide el listado sin parámetros, **Then**
   recibe 50 registros en orden ascendente de ID, empezando por el ID 1, y un cursor para la
   página siguiente.
2. **Given** el cursor devuelto por una página, **When** el cliente pide la siguiente página con
   ese cursor, **Then** los registros continúan justo después del último de la página anterior,
   sin repetir ni saltarse ninguno.
3. **Given** un tamaño de página de 200, **When** el cliente recorre todas las páginas, **Then**
   recibe 50 páginas y 9.984 registros distintos, y la última página (184 registros) trae el
   cursor nulo.
4. **Given** un cursor igual o posterior al último ID, **When** el cliente pide la página,
   **Then** recibe una página vacía con cursor nulo, no un error.
5. **Given** un registro al que le falta un dato opcional (por ejemplo, la edad), **When** aparece
   en una página, **Then** ese campo llega vacío (nulo) y el resto de sus campos con su valor.
6. **Given** una tabla con 1 millón de registros, **When** el cliente pide la primera página y una
   página al final del recorrido, **Then** las dos responden en un tiempo comparable (SC-003).

---

### User Story 2 - Filtrar registros (Priority: P1)

El cliente acota el listado con filtros: varios valores posibles para país, género, calidad de
sueño y nivel de estrés; sí o no para fumadores; y rangos mínimo/máximo para edad, tazas de café
y BMI. Los filtros se combinan entre sí y la paginación sigue funcionando sobre el resultado
filtrado.

**Why this priority**: La prueba pide filtros útiles sobre el listado; un listado sin filtros
obliga al cliente a traerse todo para encontrar algo.

**Independent Test**: Pedir el listado con un filtro conocido, recorrer todas sus páginas y
comprobar que el número de registros coincide con el valor de referencia y que todos cumplen el
filtro.

**Acceptance Scenarios**:

1. **Given** los datos cargados, **When** el cliente filtra por país `Germany`, **Then** al
   recorrer todas las páginas recibe 497 registros, todos de Germany.
2. **Given** los datos cargados, **When** filtra por país `Germany` y `Spain` a la vez, **Then**
   recibe los 983 registros de cualquiera de los dos países.
3. **Given** los datos cargados, **When** filtra por país `Germany` y género `Female`, **Then**
   recibe los 249 registros que cumplen las dos condiciones.
4. **Given** los datos cargados, **When** filtra por país escrito como `GERMANY` o `  germany `,
   **Then** recibe los mismos 497 registros que con `Germany`.
5. **Given** los datos cargados, **When** filtra por un país que no existe (`Atlantis`), **Then**
   recibe una página vacía con cursor nulo, no un error.
6. **Given** los datos cargados, **When** filtra por fumadores, **Then** recibe 2.001 registros;
   **When** filtra por no fumadores, **Then** recibe 7.983.
7. **Given** los datos cargados, **When** filtra por edad entre 30 y 40, **Then** recibe 3.540
   registros con edad de 30 a 40, ambos extremos incluidos, y ninguno de los 2 registros sin edad.
8. **Given** los datos cargados, **When** indica solo la edad mínima o solo la máxima, **Then** el
   rango queda abierto por el otro extremo.
9. **Given** los datos cargados, **When** filtra por país `Spain`, fumadores y BMI entre 25 y 30,
   **Then** recibe 26 registros.
10. **Given** un filtro cuyo resultado ocupa varias páginas, **When** el cliente lo recorre con el
    cursor, **Then** recibe cada registro que cumple el filtro exactamente una vez.
11. **Given** los datos cargados, **When** filtra por género escrito como `female` o `FEMALE`,
    **Then** recibe los mismos 4.989 registros que con `Female`.

---

### User Story 3 - Recibir errores claros ante parámetros inválidos (Priority: P2)

Cuando el cliente envía un parámetro mal formado, contradictorio o desconocido, recibe una
respuesta de error que nombra el parámetro y explica el problema, en lugar de datos equivocados o
un fallo genérico.

**Why this priority**: El servicio funciona sin esto con peticiones correctas, pero un parámetro
mal escrito que se ignora en silencio devuelve datos que el cliente cree filtrados y no lo están.

**Independent Test**: Enviar cada caso inválido de la lista y comprobar que la respuesta es un
error de validación (422) cuyo mensaje nombra el parámetro afectado.

**Acceptance Scenarios**:

1. **Given** edad mínima 50 y edad máxima 20, **When** se envía la petición, **Then** la respuesta
   es 422 y el mensaje nombra `min_age` y `max_age`.
2. **Given** un género que no existe (`Robot`), **When** se envía, **Then** la respuesta es 422,
   nombra el parámetro y muestra los valores admitidos.
3. **Given** un tamaño de página de 0, de 201 o no numérico, **When** se envía, **Then** la
   respuesta es 422 y nombra el parámetro de tamaño de página.
4. **Given** un cursor negativo o no numérico, **When** se envía, **Then** la respuesta es 422 y
   nombra el parámetro del cursor.
5. **Given** un parámetro que no existe (`min_agee`), **When** se envía, **Then** la respuesta es
   422 y nombra `min_agee`; no se ignora.
6. **Given** un valor no numérico en un rango (`min_bmi=abc`) o un valor que no es sí/no en el
   filtro de fumadores, **When** se envía, **Then** la respuesta es 422 y nombra el parámetro.
7. **Given** varios parámetros inválidos a la vez, **When** se envía, **Then** la respuesta es 422
   y nombra todos.

---

### User Story 4 - Ver un resumen por grupo (Priority: P3, opcional)

El cliente pide, con los mismos filtros del listado, un resumen agrupado por país, género,
calidad de sueño, nivel de estrés o tazas de café (en tramos de una taza). Cada grupo trae el
número de registros y los promedios de horas de sueño, tazas de café y BMI.

**Why this priority**: Da valor analítico y es donde el cliente obtiene el total de coincidencias,
pero el listado y los filtros funcionan sin él.

**Independent Test**: Pedir el resumen por género sin filtros y comprobar que los conteos de los
grupos suman 9.984 y coinciden con los valores de referencia.

**Acceptance Scenarios**:

1. **Given** los datos cargados, **When** el cliente pide el resumen por género sin filtros,
   **Then** recibe cuatro grupos: `Female` 4.989, `Male` 4.767, `Other` 225 y sin dato 3, que
   suman 9.984.
2. **Given** el resumen por género, **When** se mira el grupo `Female`, **Then** sus promedios son
   6,64 horas de sueño, 2,50 tazas de café y 23,95 de BMI.
3. **Given** el filtro de país `Germany`, **When** se pide el resumen por género, **Then** los
   conteos de los grupos suman 497.
4. **Given** los datos cargados, **When** se pide el resumen por tazas de café, **Then** hay nueve
   grupos, de 0 a 8, y el grupo `n` reúne los registros con `n` o más tazas y menos de `n + 1`.
5. **Given** una agrupación que no existe, **When** se pide el resumen, **Then** la respuesta es
   422 y nombra el parámetro.
6. **Given** filtros que no coinciden con ningún registro, **When** se pide el resumen, **Then**
   se recibe una lista de grupos vacía, no un error.

---

### User Story 5 - Consultar las filas no cargadas (Priority: P3, opcional)

Quien operó la carga consulta desde el mismo servicio las filas que no entraron: un listado
paginado con archivo, línea, motivo, columna, valor y detalle, filtrable por motivo y por
archivo, y un conteo por motivo.

**Why this priority**: La explicación de cada fila no cargada ya existe en los datos (feature
001); exponerla evita entrar a la base, pero no es necesaria para consultar los registros.

**Independent Test**: Listar las filas no cargadas del archivo actual y comprobar que son 28 y
que el conteo por motivo coincide con el resumen de la carga.

**Acceptance Scenarios**:

1. **Given** el archivo actual cargado, **When** se listan las filas no cargadas, **Then** se
   reciben 28, ordenadas por archivo y número de línea, con las mismas reglas de paginación que
   el listado de registros.
2. **Given** el filtro por motivo "no numérico", **When** se listan, **Then** se reciben 5, entre
   ellas la línea 816 con columna `Age` y valor `thirty`.
3. **Given** el filtro por archivo `coffee_health_raw.csv`, **When** se listan, **Then** se
   reciben 28; con un archivo que no existe, una página vacía.
4. **Given** el archivo actual cargado, **When** se pide el conteo por motivo, **Then** se recibe:
   fuera de rango 6, no numérico 5, número de columnas 3, booleano inválido 2, duplicado exacto 8
   y duplicado en conflicto 4.
5. **Given** un motivo que no existe, **When** se filtra por él, **Then** la respuesta es 422 y
   nombra el parámetro.

---

### Edge Cases

- Tabla vacía (antes de la primera carga): el listado devuelve una página vacía con cursor nulo y
  el resumen una lista vacía; no son errores.
- La última página queda exactamente llena (quedaban tantos registros como el tamaño de página):
  el cursor es nulo; el cliente no recibe una página vacía de más.
- Tamaño de página en los límites: 1 y 200 son válidos; 0 y 201 no.
- Cursor 0 o ausente: el recorrido empieza desde el principio.
- Mínimo igual a máximo en un rango: válido; devuelve los registros con ese valor exacto.
- Límites de rango fuera de lo plausible (edad mínima 500): no es un error; el resultado es vacío.
- Edad mínima o máxima con decimales (`30.5`): error de validación, la edad es un entero.
- El mismo filtro de selección repetido con el mismo valor: equivale a indicarlo una vez.
- Categoría escrita con otras mayúsculas o con espacios (`female`, ` GOOD `): equivale a su forma
  canónica. Una categoría que no existe (`Robot`): error de validación que muestra los valores
  admitidos.
- País vacío o solo espacios: no coincide con ningún registro; página vacía.
- Registros sin un dato opcional: se devuelven con ese campo vacío; un filtro sobre esa columna
  no los incluye; en el resumen forman un grupo propio "sin dato" y no cuentan en los promedios.
- Grupo del resumen donde ningún registro tiene un dato promediado: ese promedio llega vacío, no
  como cero.
- La base no responde o está saturada: el servicio contesta con un error temporal en tiempo
  acotado; no deja la petición colgada.
- Los datos se recargan mientras un cliente recorre las páginas: no recibe registros repetidos;
  los cambios posteriores a su posición sí pueden aparecer.
- Petición sin filtros al resumen sobre una tabla muy grande: su costo crece con el número de
  registros que cumplen los filtros; si supera el tiempo límite, se cancela y devuelve el error
  temporal (FR-030).

## Requirements *(mandatory)*

### Functional Requirements

**Listado y paginación**

- **FR-001**: El sistema DEBE ofrecer un listado de registros ordenado por ID ascendente que
  devuelve como máximo una página por petición. NO DEBE existir forma de obtener todos los
  registros en una sola respuesta.
- **FR-002**: El cliente DEBE poder indicar el tamaño de página: por defecto 50, mínimo 1 y
  máximo 200.
- **FR-003**: Cada respuesta del listado DEBE incluir los registros de la página, el tamaño de
  página aplicado y el cursor de la página siguiente, o nulo si no hay más registros.
- **FR-004**: El cursor DEBE representar la posición a partir de la cual continuar. Sin cursor, el
  listado empieza por el primer registro.
- **FR-005**: Recorrer el listado siguiendo el cursor hasta que sea nulo DEBE devolver cada
  registro que cumple los filtros exactamente una vez, sin omitir ninguno.
- **FR-006**: El cursor DEBE ser nulo exactamente cuando no quedan más registros, también cuando
  la última página queda llena. Un cursor más allá del final devuelve una página vacía.
- **FR-007**: El tiempo de respuesta de una página NO DEBE crecer con su posición en el
  recorrido: una página profunda responde como la primera. Esto se garantiza para el listado sin
  filtros y para el filtrado por país; con los demás filtros el listado funciona igual, pero su
  tiempo depende de cuántos registros coinciden.

**Filtros**

- **FR-008**: El listado DEBE aceptar filtros de selección múltiple por `country`, `gender`,
  `sleep_quality` y `stress_level`: cada uno admite uno o varios valores y coincide con
  cualquiera de ellos.
- **FR-009**: El listado DEBE aceptar un filtro booleano por `smoking`.
- **FR-010**: El listado DEBE aceptar rangos inclusivos con mínimo y máximo opcionales para `age`
  (`min_age`, `max_age`), `coffee_intake` y `bmi`. Se puede indicar solo uno de los dos extremos.
- **FR-011**: Los filtros distintos DEBEN combinarse con Y lógico; los valores de un mismo filtro
  de selección múltiple, con O lógico.
- **FR-012**: Los cuatro filtros de selección múltiple NO DEBEN distinguir mayúsculas ni espacios
  sobrantes (`female`, ` GOOD ` y `germany` equivalen a `Female`, `Good` y `Germany`). Un país
  que no existe en los datos devuelve una página vacía, no un error; una categoría que tras
  normalizar no está en su catálogo es un error de validación (FR-016).
- **FR-013**: Un filtro sobre una columna DEBE excluir los registros que no tienen ese dato.
- **FR-014**: Filtros y paginación DEBEN funcionar juntos: el cursor recorre solo los registros
  que cumplen los filtros.

**Validación de parámetros**

- **FR-015**: Ante parámetros inválidos, el sistema DEBE responder con un error de validación
  (422) cuyo mensaje nombra cada parámetro afectado, y NO DEBE devolver datos.
- **FR-016**: Son inválidos: un mínimo mayor que su máximo; una categoría desconocida en `gender`,
  `sleep_quality` o `stress_level`; un tamaño de página fuera de 1–200 o no entero; un cursor no
  numérico o negativo; un extremo de rango no numérico (o no entero, en la edad); un valor no
  booleano en `smoking`.
- **FR-017**: Un parámetro desconocido (por ejemplo `min_agee`) DEBE producir un error de
  validación que lo nombra; nunca se ignora en silencio.
- **FR-018**: Si hay varios parámetros inválidos en la misma petición, la respuesta DEBE
  nombrarlos todos.

**Formato de respuesta**

- **FR-019**: Cada registro DEBE exponer su ID y sus 15 atributos de origen (edad, género, país,
  tazas de café, cafeína, horas de sueño, calidad de sueño, BMI, frecuencia cardiaca, nivel de
  estrés, horas de actividad física, problemas de salud, ocupación, si fuma y si consume
  alcohol).
- **FR-020**: Los valores numéricos DEBEN devolverse como números JSON, no como cadenas; los
  booleanos como booleanos; y un dato que falta como nulo, nunca como cadena vacía ni como cero.

**Resumen por grupo (opcional)**

- **FR-021**: El sistema DEBE ofrecer un resumen que acepta los mismos filtros que el listado y
  agrupa por uno de: país, género, calidad de sueño, nivel de estrés o tazas de café.
- **FR-022**: Cada grupo DEBE incluir su clave, el número de registros y los promedios de horas de
  sueño, tazas de café y BMI, redondeados a dos decimales.
- **FR-023**: La agrupación por tazas de café DEBE usar tramos de una taza: el grupo `n` reúne los
  registros con al menos `n` tazas y menos de `n + 1`.
- **FR-024**: Los registros sin el dato de agrupación DEBEN formar un grupo propio con clave
  vacía, de modo que la suma de los conteos coincida con el total de registros que cumplen los
  filtros. Los promedios ignoran los datos que faltan; un promedio sin ningún dato es nulo.
- **FR-025**: El resumen DEBE devolver todos los grupos en una sola respuesta, ordenados por
  clave. Una agrupación desconocida produce un error de validación (FR-015).

**Filas no cargadas (opcional)**

- **FR-026**: El sistema DEBE ofrecer un listado paginado de las filas no cargadas, ordenado por
  archivo y número de línea, con las mismas reglas de tamaño de página y cursor que el listado de
  registros (FR-002 a FR-007).
- **FR-027**: Cada fila no cargada DEBE exponer: archivo de origen, número de línea, motivo,
  columna afectada, valor original y detalle.
- **FR-028**: Ese listado DEBE poder filtrarse por motivo (uno o varios de los nueve motivos de la
  feature 001) y por archivo. Un motivo desconocido es un error de validación; un archivo que no
  existe devuelve una página vacía.
- **FR-029**: El sistema DEBE ofrecer el conteo de filas no cargadas por motivo, filtrable por
  archivo.

**Disponibilidad y escala**

- **FR-030**: Si la base no puede atender la petición (saturada o caída) o la consulta tarda
  demasiado, el sistema DEBE responder con un error temporal claro (servicio no disponible, 503);
  NO DEBE dejar la petición colgada. El límite es de 5 segundos por defecto, configurable, y
  aplica por separado a la espera por la base y a la ejecución de la consulta, que se cancela al
  superarlo.
- **FR-031**: El listado sin filtros, el filtrado por país y el listado de filas no cargadas
  DEBEN seguir cumpliendo FR-007 con decenas de millones de registros; el uso que el servicio hace de la base DEBE estar
  acotado y ser configurable, de modo que un pico de peticiones no la sature.
- **FR-032**: La API es de solo lectura: ninguna petición de esta feature modifica datos.

**Documentación interactiva**

- **FR-033**: Todas las consultas de esta feature DEBEN poder ejecutarse desde la documentación
  interactiva del servicio, que DEBE mostrar cada parámetro con su tipo, valor por defecto,
  límites y valores admitidos, y la forma de cada respuesta.
- **FR-034**: Las consultas que el servicio ya ofrece (estado y notas) DEBEN seguir funcionando.

### Key Entities

- **Registro de salud**: una persona del dataset, tal como la dejó la feature 001. ID, país,
  tazas de café y cafeína siempre tienen valor; los otros 12 atributos pueden estar vacíos.
- **Página**: el resultado de una petición de listado: una lista acotada de elementos, el tamaño
  de página aplicado y el cursor de la siguiente página (o nulo).
- **Filtros**: el conjunto de condiciones que acota un listado o un resumen: selecciones
  múltiples, un booleano y rangos.
- **Grupo de resumen**: una clave de agrupación con su número de registros y sus tres promedios.
- **Fila no cargada**: una línea del archivo de origen que no llegó a ser registro, con su
  archivo, línea, motivo, columna, valor y detalle (feature 001).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Con cualquier tamaño de página entre 1 y 200, recorrer el listado completo devuelve
  exactamente 9.984 registros distintos en orden ascendente de ID.
- **SC-002**: Ninguna respuesta de listado contiene más de 200 elementos.
- **SC-003**: Con 1 millón de registros, la página más profunda del listado tarda como máximo el
  doble que la primera, y ambas menos de 200 ms en un equipo de desarrollo, sin filtros y
  filtrando por un país.
- **SC-004**: El 100 % de los casos inválidos de FR-016 y FR-017 devuelve un error de validación
  que nombra el parámetro; ninguno devuelve datos ni un fallo interno.
- **SC-005**: El 100 % de los valores numéricos de las respuestas son números JSON; ninguno llega
  como cadena.
- **SC-006**: Para cualquier combinación de filtros, la suma de los conteos del resumen es igual
  al número de registros que devuelve el listado con esos mismos filtros (9.984 sin filtros).
- **SC-007**: El listado de filas no cargadas del archivo actual devuelve 28 filas y su conteo
  por motivo coincide con el resumen de la carga (6, 5, 3, 2, 8 y 4).
- **SC-008**: Con la base saturada o caída, el cliente recibe el error temporal en 6 segundos
  como máximo (el límite de 5 más el margen de respuesta). Una consulta que lleva más de 5
  segundos ejecutándose se cancela y devuelve el mismo error.
- **SC-009**: Una persona que no conoce el servicio puede listar, filtrar, pasar de página y ver
  el resumen usando solo la documentación interactiva, sin otra herramienta.

## Assumptions

- Los datos los deja la feature 001 y solo cambian con una nueva carga; la API no escribe.
- Sin autenticación: el servicio se usa en local para la prueba. Queda fuera de alcance.
- Los registros sin un dato no se pueden pedir expresamente: no hay filtro "sin dato". Se ven en
  el listado sin filtrar por esa columna y en el grupo "sin dato" del resumen.
- La documentación interactiva muestra las categorías de `gender`, `sleep_quality` y
  `stress_level` en su forma canónica (`Female`, `Good`, `Low`); escritas de otra forma se
  aceptan igual (FR-012). Es la misma normalización que aplica la carga.
- El filtro de fumadores acepta las formas habituales de verdadero y falso.
- El cursor es el último ID recibido. El cliente lo devuelve tal cual; cualquier entero no
  negativo es un cursor válido.
- El listado no devuelve el total de coincidencias: contarlas en cada página costaría recorrer
  todos los registros que cumplen el filtro. El total lo da el resumen.
- El resumen no se pagina: el número de grupos es pequeño y acotado (como mucho 20 países o unas
  decenas de tramos de tazas). Su costo sí crece con los registros que cumplen los filtros; se
  acepta por ser una consulta analítica opcional, acotada por el tiempo límite de FR-030.
- Los promedios se redondean a dos decimales.
- El listado de filas no cargadas no incluye la fila original completa; quien la necesite la
  consulta en la base.
- SC-003 se mide con una tabla sintética de 1 millón de registros, generada con la herramienta
  de escala de la feature 001. Fuera de esos dos casos (FR-007), un filtro con muy pocas
  coincidencias puede tardar más; se documenta como compromiso en el plan.
- El límite de 5 segundos deja margen amplio a cualquier consulta normal (el listado tarda
  milisegundos) y corta pronto lo que no va a terminar.
- Los nombres exactos de los parámetros de paginación y la estructura de cada respuesta se fijan
  en el contrato del plan.
- Fuera de alcance: escritura o edición, autenticación, ordenamientos arbitrarios, total de
  coincidencias en el listado, front end.
