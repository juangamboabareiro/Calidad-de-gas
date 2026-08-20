# CHANGELOG


## Primer Semana
La idea fue entender el excel, la dinamica y sus relaciones con las hojas para lograr el modelado.

La idea es en primer lugar la migracion total del proyecto a python para lograr replicar/automatizar el funcionamiento del excel.

Para esto tome las hojas de inputs que procese para dejarlas como se documenta. Algunas hojas de entrada manual las tome con los datos entregados.


La hoja DICC fue separada en diversas hojas para organizar de manera mas optima la informacion

La hoja Iny 2026/2030 fue separada segun tipo de inyeccion por lo que se transfromo en 3 hojas distintas (Yacimientos, Flujos directos, detalles_hubs)




Progreso:

- Migracion de las hojas Iny 2026/2030


Ahora:

- Completar Migracion de hojas Inyeccion, Iny Areas


Consultas:

- Agregar columna operador a MAPA o donde?
- Tratar tema de la matriz de inyeccion que aunque este funcional no se si es el mejor input
- Charlar sobre como se trabajaron los inputs y normalizar sobre el tratamiento presubida
- Recomendar una vision mas general sobre los archivos en el sentido capaz es mas recomendable tener hojas completas sobre yacimientos/areas, plantas, gasoductos, cromatos


## 30/6/26

- Normalizacion de titulos str
- Normalizacion de tipos de datos numericos
- Creacion de yacimientos_areas que es yacimientos mas HUB

## 1/7/26

- Transformacion de la DB de values a type datetyme
- Merge HUB a inyeccion

## 2/7/26

- Creacion de Inyeccion_area lograda para yacimientos (inyeccion primaria)
- Merge Inyeccion_Area, Yacimientos_area


## 3/7/26

- Mergear las demas tablas que conforman Inyeccion_area con Inyecciones 
- Detalle HUBs tiene Hubs pero flujos directos no por eso existen yacimientos_areas, detalles_hubs_areas
- Para simplificar cada merge es propio osea hay un inyeccion_yacimientos_areas, inyeccion_detalles_hubs_areas e inyeccion_flujos_directos_areas
- Inyeccion_flujos_directos esta medio manula habria que revisar luego
- Terminado INY AREAS
- Comienzo a cargar el modelo de las plantas y con YAC's
- Agrego hoja input de los coefs correspondientes a INY AREAS

La columna de produccion total la tengo que armar consultando a la database de values para una fecha especifica y usando los coefs estos para ver el porcentaje del total de inyeccion que representa cada gasoducto


## TODO : Armar yacimientos que alimenta todos los modelos de planta



## Consultas:
- Los coeficientes que aparecen en INY AREAS para poder arracncar con la hoja YAC's
- Como modelamos la matriz inyeccion 2026/2030



## 7/7/26
- Arranque con la construccion de la tabla yacimientos denominada tabla total
- Finalizada tabla YAC's para la seccion de yacimientos de INY AREAS utilice un periodo en particular ya que fue armada con queries



## A considerar
- A partir de aca veo que aparecen las consultas que se hacen en show y que la tabla total depende de la consulta por lo que tendria que armar una funcion de consulta
- Esquema de validaciones
- Consultar sobre valores calculados por ejemplo en propiedades que no parecen pesar mucho y hasta parece ser un web scrapping


## TODO
- Ver que falta en YAC's por tema datos cromato
- Arrancar con el modelado de las plantas y documentacion


## 8/7/26
- Note que en el dataset final de YAC's osea tabla total faltaban rows y cambie los hows de los merges de inner a left
- Agregue valores de croma para completar operaciones
- Falta agregar coso de GPA pero antes hay que trabajar sobre propiedades para agregar los valores calculados

## TODO Proxima setimana

Definir de manera aprox los esquemas grales como inputs arq flujo y dudas a consultar por posible reunion


## 13/7/26
- Comence con el calculo de los valores z, PCS, IW pero note que habia un error en las rows porque tenia gasoductos repetidos
- El error venia del mergeado de los dataframes particularmente en los coefs de iny
- Pude arreglarlo asi que ahora sigo con el calculo de los params GPA
- Revisar que faltan el calulo de  PCS y IW hay un tema con Nans y acomodar el codigo horrible obvio



## 15/7/26
- Completados los datos de GPA para yacimientos
- Reescribi todo para poder utilizar la funcion dot de pandas usando compuesto como index
- Ahora toca armar las demas inyecciones osea detalles_hubs y flujos directos




## TODO
- URGEEENTEEE REVISAR LOS MERGEOS DE INY_....._AREA para los 3 porque hay algunos doblemente mergeados
- Deberia ver cuales son los parametros con los que se entra a SHOW para ya ir armando las queries



## 16/7/26
- Transforme las cuentas de gas en una funcion sobre df
- Voy con las plantas. Para el gas que ingresa tengo que usar la matriz de inyeccion que me da los gasoductos que llevan a cada planta
- Note que tanto flujos directos como detalles hubs estaban medio para atras asi que toca arreglarlos. Inyeccion primaria que seria yacimientos esta todo en orden



## 20/7/26
- Concluida la construccion de la tabla flujos_directos
- Armado de temas a presentar en reunion
- Primer ataque a las tablas de HUB's



## 21/7/26
- Armado del README.md



## 22/7/26
- Presentacion de avances
- Seguimos ocn detalles_hubs y flujos_directos


## 24/7/26
- Comienzo modelado de plantas
- Revisar en detalles_hubs cuadno quise modelar TTY el ingreso lo saco de la MI el tema es que para sacar lo que ingresa reviso HUB's y entiendo saco todo de YAC's osea INY 2026 dios sabra que pasa


## 27/7/26


## 28/7/26
- Comence de cero el modelado de plantas ya que lo estaba haciendo con detalles_hubs pero debia hacerse con flujos_directos. Asumi las cromato de Gasoductos como input de HUBs


## 29/7/26
- Reunion de consultas
- Documentacion de las tablas


## 30/7/26
- Ponerse a ver que onda la tabla de propiedades que hay que tratarla entera como input
- Hoy estoy terminando el modelado de plantas. Ya tengo toda la tabla principal ahora estoy con la logica de la reestriccion.
- Estoy replicando las correcciones que se hacen segun si F9 que es ene-2025 es menor a la fecha random puesta ahi que supongo que es alguna ampliacion. Luego de eso se recalcula todo.


## 31/7/26
- Termine el modelado de la planta TTY(DP). TTY(TBX) es similar. TBX EP no tiene problemas ni reestrccion de capacidad por ahora y MEGA hay que corregir toda la entrada.
- Comence a modularizar el archivo principal. ctes_gas.py, data_io.py, local_functions.py. Falta definir bien transform.py.


## 03/8/26
- Modulos fuerte llegando hasta las plantas igualmente hay que acomodar
- Por alguna razon se volvio a romper gas_rico_IN asi que revisar y recordar que hay importaciones solo para tratar este error
- Por otro lado las hojas estan horribles algunas digo los modulos asi que revisar xd
- Revisar la funcion de props de gas que ahi vi que faltaba una normalizacion en coefs_inys_area y por ello no andaba el merge para los coefs de iny. esto capaz afecta a la tabla de tty


## 04/8/26
- Arreglado el problemilla de la tabla_total_yacimientos y la funcion de calcular_propiedades_gas. Igual hay que revisar porque algunos resultados no me estan matcheando.
- Ahora voy a seguir con las plantas
- VER PARA USAR CONSTANTE* para las funciones que tienen las constantes 
- Ya esta el ciclo de retenidos para las plantas osea ya puedo corregir los coefs y volver a correr el modelado el temita es que me estan dando negativos algunos retenidos que creo que no es del todo malo pero mepa que tiene que ver con capacidad vs volumen inyectado porque eso queda estatico y ahora yo le meto nuevos coefs. Puede que me equivoque y no sea esto queda para chequear mañana o el jueves :p



## 05/8/26
- Arregle el problema de z pero encontre que hay una mini discrepancia entre los valores debido a los decimales truncados en los inputs de las cromatos --> IMPORTANTE TRATAR


## 07/8/26
- Tengo ya digamos version final 1.0 de planta template que va para cualquier planta y ya estarian terminadas las TTY solo hay que revisar ---> BUTANOS CREO HAY ALGO RARO PORQUE NO SE SI ESTOY USANDO LA SUMA O LOS VALUES EN VERDAD
- Quedaria MEGA y listo. DSP tema revisar unidades, replantear modelos, documentar y capa frontend aunque yo arrancaria con revisar y frontend.

## 13/8/26
- Estoy finalizando el modelado de las plantas TTY, para la logica de las mismas como hay parametros que deben ser editables y ejecutables. Ejemplo: Ampliaciones, periodos de mantenimiento etc voy a considerar la planta como una unidad funcional y los parametros alterables seran llamados aparte. Ejemplo: TTY_TBX se pondra en marcha y luego se amplia, la funcion a generar para TTY solo considera una capacidad total que dsp sera alterada con una funcion auxiliar AMPLIAR_PLANTA que modificara el input de la funcion MODELAR_TTY
- Tengo que agregar el volumen y las cromas de las desviaciones a mega y tty_dp
- Bueno listo tengo que ver como resuelto el tema de la derivacion nada mas para agregar las cromas y el volumen

## 19/8/26
- Ya creo que esta la mayoria pero noto que TTY no bypassea por alguna razon asi que debo revisar esos temillas y hacerle pruebas, normalizar inputs y documentar (CLAUDE)