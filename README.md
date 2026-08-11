# Migración Modelo Acondicionamiento de gas CN-VM



## Esquema Excel


```mermaid
flowchart TD
    MAPA[Mapa] -->|key: Area| I[Inyeccion]
    VALS[Values] -->|key: Area -> Volumen| I
    DIC[Diccionario] -->|key: Area -> Hub| I
    DIC -->|key: Area -> Hub| I2026[Iny 2026]
    I2026 --> Y[Yacimientos]
    VALS -->|key: Area -> Prod. Total| Y
    PA[Premisas Areas] -->|key: Area -> Cromatografia| Y
    I --> Y
    PROPS[Propiedades] -->|Constantes y GPA| Y

    classDef input fill:#d4f4dd,stroke:#2e7d32,color:#000;
    class MAPA,VALS,DIC,PROPS,PA input;

```






## Esquema Python


```mermaid


flowchart TD

    ISTD[inyeccion_std] <--> I9300[inyeccion_9300]
    COEFS[coeficientes] <--> ISTD
    YA[yacimientos_areas] <-->|merge: Area -> Hub| Y[yacimientos]
    YA <-->|merge: Area -> Hub| PY[plantas_yacimientos]
    I[inyeccion] <-->|groupby: Anio| ISTD
    I <-->|merge: Area -> Hub| PY
    IA[inyeccion_area] <-->|merge: Area -> Gasoducto/Destino| I
    IA <-->|merge: Area -> Gasoducto/Destino| MI[matriz_inyecciones]
    IYA[inyeccion_yacimientos_areas] <-->|merge: Area,Gasoducto -> Volumen Inyectado| YA
    IYA <-->|merge: Area,Gasoducto -> Volumen Inyectado| IA
    DH[detalles_hubs] <-->|merge: Area -> Hub| PY
    DHA[detalles_hubs_areas] <-->|merge: Area -> Hub| DH
    IDHA[inyeccion_detalles_hubs_areas] <-->|merge: Area,Gasoducto -> Volumen Inyectado| MI
    IDHA <-->|merge: Area,Gasoducto -> Volumen Inyectado| DHA
    FD[flujos_directos] <-->|merge: Area -> Hub| IFD[inyeccion_flujos_directos]
    IFD <-->|merge: Area -> Hub| MI
    CIA[coefs_inyeccion_area_query] <--> TTY
    TTY[tabla_total_yacimientos] <-->|trae: Area,Gasoducto| IYA
    TTY <-->|merge: Area -> query-Periodo| ISTD
    PA[premisas_areas] <-->|merge: Area -> Cromatografia| TTY
    PROPS[propiedades] <--> TTY
    CTESGAS[constantes_GAS] <--> PROPS
    CTESGAS <--> TTY
    DHA <-->|merge: Area -> HUB| PY

    TTFD <-->|Iguales| IFD
    TTFD[tabla_total_flujos_directos] <-->|merge: Area -> Volumen_inyectado| CIA
    TTFD <--> PA
    TTFD <--> PROPS
    TTFD <--> CTESGAS

    TTDH[tabla_total_detalles_hubs] <--> IDHA
    TTDH <--> PA
    TTDH <--> PROPS
    TTDH <--> CTESGAS

    TTF[tabla_total_final] <-->|Inyeccion Primaria| TTY
    TTF <-->|Inyeccion Resto| TTDH
    TTF <-->|HUB| TTFD



    classDef input fill:#d4f4dd,stroke:#2e7d32,color:#000;
    classDef output fill:#FFB74D,color:#FFF,stroke:#FFA500;
    classDef output_final fill:#0D0847,color:#FFF,stroke:#000;
    class TTF output_final;
    class TTY,TTFD,TTDH output;
    class COEFS,I9300,PA,PROPS,MI,FD,Y,DH,CIA,PY,CTESGAS input;

```




## Esquema Python simplificado

```mermaid


flowchart TB



    subgraph Detalles_Hubs
        DHA[detalles_hubs_area]
        IDHA[inyeccion_detalles_hubs_area]
    end

    subgraph Flujos_Directos
        IFD[inyeccion_flujos_directos]
    end

    subgraph Yacimientos
        YA[yacimientos_area]
        IYA[inyeccion_yacimientos_area]
    end

    subgraph Tablas_Intermedias
        I[inyeccion]
        IA[inyeccion_area]
    end

    YA <--> IYA
    DHA <--> IDHA
    I <--> IA


    subgraph Inputs
      direction TB
      COEFS[coeficientes]
      I9300[inyeccion_9300]
      PA[promisas_areas]
      PROPS[propiedades]
      MI[matriz_inyecciones]
      FD[flujos_directos]
      Y[yacimientos]
      DH[detalle_hubs]
      CIA[coefs_inyeccion_area]
      PY[plantas_yacimientos]
      CTESGAS[constantes_GAS]
    end



    subgraph Outputs
        direction TB
        TTY[tabla_total_yacimiento]
        TTFD[tabla_total_flujos_directos]
        TTDH[tabla_total_detalles_hubs]
    end

    

    Inputs <---> Yacimientos
    Inputs <---> Flujos_Directos
    Inputs <---> Detalles_Hubs
    Yacimientos <----> Tablas_Intermedias
    Flujos_Directos <----> Tablas_Intermedias
    Detalles_Hubs <----> Tablas_Intermedias
    Yacimientos <------> Outputs
    Flujos_Directos <------> Outputs
    Detalles_Hubs <----> Outputs      

    TTF[tabla_total_final - Simil Yacimientos] <----> Outputs

    classDef input fill:#d4f4dd,stroke:#2e7d32,color:#000;
    classDef output fill:#FFB74D,color:#FFF,stroke:#FFA500;
    classDef output_final fill:#0D0847,color:#FFF,stroke:#000;
    class TTF output_final;
    class TTY,TTFD,TTDH output;
    class COEFS,I9300,PA,PROPS,MI,FD,Y,DH,CIA,PY,CTESGAS input;

```



## Inputs 
| Sheet Excel original | Descripción | Reemplazado por |
|---|---|---|
| `Values` | Separe los valores de iny a 9300 y los coefs para std | `coeficientes, inyeccion_9300` |
| `Mapa` | Misma tabla | `mapa` |
| `Premisas area` | Mismos valores | `premisas_areas` |
| `Propiedades` | Separe los valores de ctes de gas y solo me quede con los no calculados | `propiedades, constantes_GAS` |
| `Diccionario` | Separe la matriz de origen-destino de plantas y gasoductos y el listado de HUB's | `matriz_inys, plantas_yacimientos` |
| `Inyeccion 2026` | Separe por tipo de inyeccion y aparte el detalle de hubs | `yacimientos, detalles_hubs, flujos_directos` |
| `Coeficientes inyeccion area` | Coefs de inyeccion area para la dinamica | `coefs_iny_area` |









## Estructura del proyecto


```
proyecto/
├── config.py                  # paths, capacidad, periodo_considerado, fecha_random
├── io_/
│   └── loaders.py             # funciones (no module-level!) para leer cada sheet,
│                               # con path configurable. Ej: load_constantes_gas(path)
├── domain/
│   ├── normalizacion.py       # normalizar, asignar_estacion
│   ├── propiedades_gas.py     # calcular_propiedades_gas
│   └── retenidos.py           # calcular_retenidos, calcular_energia_total, correcciones
├── pipeline/
│   ├── preprocesamiento.py    # fillna + normalizar sobre inputs crudos
│   ├── inyeccion.py           # inyeccion_std, inyeccion, inyeccion_area
│   ├── yacimientos.py         # yacimientos_areas, inyeccion_yacimientos_areas
│   ├── detalles_hubs.py
│   ├── flujos_directos.py
│   ├── tabla_total.py         # tabla_total_yacimientos + tabla_total_flujos_directos
│   │                           # (comparten ~90% de lógica, se puede parametrizar)
│   └── modelado_plantas.py    # tabla_mega, tabla_tty_dp, retenidos, correcciones
├── outputs/
│   └── writers.py             # to_csv centralizado, opcional (flag para activar/desactivar)
└── main.py                    # orquesta: carga → preprocesa → pipeline → outputs
```


```
calidad-gas/
├── datos/
│   ├── inputs.xlsx     # excels de input originales
│   └── processed/      # outputs intermedios
├── src/
│   ├── io.py                # lectura de inputs
│   ├── modelo.py            # lógica de cálculo
│   └── validaciones.py      
├── docs/
│   ├── data_dictionary.md    # info de normalizacion de inputs y tipo de datos
│   └── changelog.md          # decisiones e historial de cambios
|
├── capa_frontend_streamlit/
|
└── README.md
```



## Flujo
- Normalizo iny 9300 a std dividiendo con los coefs
- Creo 
  - yacimientos_areas: Merge de yacimientos(inyeccion primaria) con plantas_yacimientos(HUB's)
- Trabajo sobre la serie temporal en inyeccion std para sacar el promedio por año(Aca no se que tan importante es la estacion?)
- Creo
  - inyeccion: inyeccion_std grupeada por valor medio segun año
- Mergeo inyeccion con plantas_yacimientos para agragar HUBS
- Creo
  - inyeccion_area: Merge de inyeccion con matriz_inyecciones para añadir el destino/gasoductos
- Creo
  - inyeccion_yacimientos_area: Merge inyeccion_area con yacimientos_area para agregar el volumen inyectado a cada destino
- Los ultimos dos pasos los repito con detalles_hubs y flujos_directos
- Selecciono un periodo particular y comienzo con las tablas_total que serian las YAC's 



## Consultas
- Hojas HUB's. ¿Como es la logica y sobre produccion total? Cromas gasoductos [Flujos de áreas directo a gasoductos, Flujos totales gasoductos]
- Hoja propiedades.
- ¿Como tratamos la matriz de inyeccion?
- Consultar sobre el modelado de las plantas. ¿Donde entran los datos de yacimientos? 
- Hay algunas inconsistencias en yacimientos inyeccion primaria. Del script de validacion saltan en cromas
- Veo que la inyeccion secundaria en yacimientos es con query entonces no me termina de cerrar donde entra la matriz de inyeccion
- Los datos de cromato usados para TTY DP no son vmn y vms eso hay que cambiarlo. yo agarre lo de las areas que son las que conectan a TTY DP y use eso pero no es la idea
- Sobre las cromatos por ejemplo en aquellas que dicen nombre, nombre TBX. Entiendo que una es pre y otra post la consulta apunta a cual utilizo para por ejemplo cuando modelo la planta TTY (DP)



## TODO
- Agregar reestriccion a la capacidad de gasoductos y logica de evacuacion del gas







```mermaid

flowchart TB

START([Flujo_entrante])



MI[Matriz_inyecciones] <--> Y[Yacimientos]



```




Me entra el dato de flujo total segun area que lo saco de yacimientos. Las areas salen de matriz_iny. De props me saco los datos del gas y dsp me tengo que traer la data del area de cromato osea aca composicion molar pero es del area en si.