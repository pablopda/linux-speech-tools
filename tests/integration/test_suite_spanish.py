#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spanish Test Suite for Chunking Algorithm Evaluation
Contains 20 diverse test cases with gold standard ideal chunks
"""

SPANISH_TEST_SUITE = [
    {
        "id": 1,
        "name": "Oraciones Simples",
        "text": "El gato se sentó en la alfombra. Fue un día hermoso. El sol brillaba intensamente.",
        "ideal_chunks": [
            "El gato se sentó en la alfombra. Fue un día hermoso. El sol brillaba intensamente."
        ]
    },
    {
        "id": 2,
        "name": "Oración Compleja con Cláusulas Subordinadas",
        "text": "Aunque el tiempo estaba terrible, decidimos ir de excursión porque habíamos planeado este viaje durante meses, y no queríamos decepcionar a los niños.",
        "ideal_chunks": [
            "Aunque el tiempo estaba terrible, decidimos ir de excursión porque habíamos planeado este viaje durante meses, y no queríamos decepcionar a los niños."
        ]
    },
    {
        "id": 3,
        "name": "Abreviaciones y Títulos",
        "text": "El Dr. García, especialista en medicina, trabaja para el Ministerio de Salud de EE.UU. Se graduó de la Universidad de Madrid en 2010.",
        "ideal_chunks": [
            "El Dr. García, especialista en medicina, trabaja para el Ministerio de Salud de EE.UU. Se graduó de la Universidad de Madrid en 2010."
        ]
    },
    {
        "id": 4,
        "name": "Listas y Enumeraciones",
        "text": "Necesitamos comprar manzanas, naranjas, plátanos y uvas. Además, deberíamos recoger pan, leche y queso de la sección de lácteos.",
        "ideal_chunks": [
            "Necesitamos comprar manzanas, naranjas, plátanos y uvas.",
            "Además, deberíamos recoger pan, leche y queso de la sección de lácteos."
        ]
    },
    {
        "id": 5,
        "name": "Números y Fechas",
        "text": "La reunión está programada para el 15 de enero de 2024, a las 15:30 horas. Esperamos aproximadamente 125 asistentes.",
        "ideal_chunks": [
            "La reunión está programada para el 15 de enero de 2024, a las 15:30 horas.",
            "Esperamos aproximadamente 125 asistentes."
        ]
    },
    {
        "id": 6,
        "name": "Preguntas y Exclamaciones con Signos Invertidos",
        "text": "¿Estás listo para la presentación? ¡Espero que sí! Hemos trabajado muy duro en este proyecto.",
        "ideal_chunks": [
            "¿Estás listo para la presentación? ¡Espero que sí!",
            "Hemos trabajado muy duro en este proyecto."
        ]
    },
    {
        "id": 7,
        "name": "Contenido Técnico",
        "text": "El protocolo HTTP utiliza el puerto 80 para conexiones estándar. HTTPS, sin embargo, utiliza el puerto 443 y proporciona comunicación cifrada mediante certificados SSL/TLS.",
        "ideal_chunks": [
            "El protocolo HTTP utiliza el puerto 80 para conexiones estándar. HTTPS, sin embargo, utiliza el puerto 443 y proporciona comunicación cifrada mediante certificados SSL/TLS."
        ]
    },
    {
        "id": 8,
        "name": "Oración Larga que Requiere División",
        "text": "El informe exhaustivo, que fue presentado por el equipo de investigación después de meses de recolección intensiva de datos y análisis, demuestra claramente que la nueva metodología produce resultados significativamente mejores que los enfoques tradicionales, especialmente cuando se aplica a conjuntos de datos a gran escala con interdependencias complejas.",
        "ideal_chunks": [
            "El informe exhaustivo, que fue presentado por el equipo de investigación después de meses de recolección intensiva de datos y análisis, demuestra claramente que la nueva metodología produce resultados significativamente mejores que los enfoques tradicionales,",
            "especialmente cuando se aplica a conjuntos de datos a gran escala con interdependencias complejas."
        ]
    },
    {
        "id": 9,
        "name": "Diálogo y Comillas",
        "text": "Ella dijo: \"Creo que deberíamos salir temprano\". Su amiga respondió: \"Es una buena idea, pero terminemos nuestro café primero\".",
        "ideal_chunks": [
            "Ella dijo: \"Creo que deberíamos salir temprano\".",
            "Su amiga respondió: \"Es una buena idea, pero terminemos nuestro café primero\"."
        ]
    },
    {
        "id": 10,
        "name": "Frases Transicionales",
        "text": "Primero, necesitamos recopilar todos los requisitos. Luego, crearemos un documento de diseño detallado. Finalmente, podremos comenzar la fase de implementación.",
        "ideal_chunks": [
            "Primero, necesitamos recopilar todos los requisitos.",
            "Luego, crearemos un documento de diseño detallado.",
            "Finalmente, podremos comenzar la fase de implementación."
        ]
    },
    {
        "id": 11,
        "name": "Subjuntivo y Condicionales",
        "text": "Si mejora el tiempo mañana, iremos a la playa; de lo contrario, nos quedaremos en casa viendo películas. Es posible que llueva, pero esperamos que escampe.",
        "ideal_chunks": [
            "Si mejora el tiempo mañana, iremos a la playa; de lo contrario, nos quedaremos en casa viendo películas.",
            "Es posible que llueva, pero esperamos que escampe."
        ]
    },
    {
        "id": 12,
        "name": "Texto Científico con Grados",
        "text": "El estudio examinó los efectos de la temperatura en la actividad enzimática. A 25°C, la enzima mostró un rendimiento óptimo, pero a 45°C, la actividad disminuyó un 30%.",
        "ideal_chunks": [
            "El estudio examinó los efectos de la temperatura en la actividad enzimática.",
            "A 25°C, la enzima mostró un rendimiento óptimo, pero a 45°C, la actividad disminuyó un 30%."
        ]
    },
    {
        "id": 13,
        "name": "Múltiples Abreviaciones",
        "text": "La Sra. Martínez, directora ejecutiva, se reunió con representantes de la NASA, el FBI y los EE.UU. para discutir el proyecto clasificado.",
        "ideal_chunks": [
            "La Sra. Martínez, directora ejecutiva, se reunió con representantes de la NASA, el FBI y los EE.UU. para discutir el proyecto clasificado."
        ]
    },
    {
        "id": 14,
        "name": "Puntuación Mixta",
        "text": "¡Los resultados fueron increíbles! (Logramos una tasa de éxito del 95%.) Sin embargo, aún necesitamos abordar algunos problemas menores; específicamente, el tiempo de carga podría mejorarse.",
        "ideal_chunks": [
            "¡Los resultados fueron increíbles! (Logramos una tasa de éxito del 95%.)",
            "Sin embargo, aún necesitamos abordar algunos problemas menores; específicamente, el tiempo de carga podría mejorarse."
        ]
    },
    {
        "id": 15,
        "name": "Narrativa con Referencias Temporales",
        "text": "Ayer por la mañana, Sarah se despertó a las 6:00 A.M. e inmediatamente comenzó a prepararse para su importante entrevista de trabajo. Había estado esperando esta oportunidad durante semanas.",
        "ideal_chunks": [
            "Ayer por la mañana, Sarah se despertó a las 6:00 A.M. e inmediatamente comenzó a prepararse para su importante entrevista de trabajo.",
            "Había estado esperando esta oportunidad durante semanas."
        ]
    },
    {
        "id": 16,
        "name": "Análisis Comparativo",
        "text": "Mientras que el Método A proporciona resultados más rápidos, el Método B ofrece mayor precisión. Por lo tanto, la elección depende de si la velocidad o la precisión es más importante para su caso de uso específico.",
        "ideal_chunks": [
            "Mientras que el Método A proporciona resultados más rápidos, el Método B ofrece mayor precisión.",
            "Por lo tanto, la elección depende de si la velocidad o la precisión es más importante para su caso de uso específico."
        ]
    },
    {
        "id": 17,
        "name": "Oraciones Muy Cortas",
        "text": "Para. Escucha atentamente. Esto es importante. Debemos actuar ahora.",
        "ideal_chunks": [
            "Para. Escucha atentamente. Esto es importante. Debemos actuar ahora."
        ]
    },
    {
        "id": 18,
        "name": "Comillas Anidadas",
        "text": "Juan dijo: \"María me contó: 'La fecha límite se ha trasladado al viernes', pero no estoy seguro de que tenga razón\".",
        "ideal_chunks": [
            "Juan dijo: \"María me contó: 'La fecha límite se ha trasladado al viernes', pero no estoy seguro de que tenga razón\"."
        ]
    },
    {
        "id": 19,
        "name": "URLs y Correo Electrónico",
        "text": "Por favor, visite nuestro sitio web en www.ejemplo.com o envíe un correo electrónico a soporte@empresa.org para obtener más información.",
        "ideal_chunks": [
            "Por favor, visite nuestro sitio web en www.ejemplo.com o envíe un correo electrónico a soporte@empresa.org para obtener más información."
        ]
    },
    {
        "id": 20,
        "name": "Discusión Técnica Compleja",
        "text": "El modelo de aprendizaje automático utiliza una arquitectura de red neuronal convolucional (CNN) con normalización por lotes y capas de abandono. Durante el entrenamiento, observamos que la precisión de validación se estabilizó en aproximadamente 87.5% después de 50 épocas. Para mejorar el rendimiento, implementamos técnicas de aumento de datos incluyendo rotación, escalado y volteo horizontal, lo que resultó en una precisión final del 92.3%.",
        "ideal_chunks": [
            "El modelo de aprendizaje automático utiliza una arquitectura de red neuronal convolucional (CNN)",
            "con normalización por lotes y capas de abandono. Durante el entrenamiento, observamos que la precisión de validación se estabilizó en aproximadamente 87.5% después de 50 épocas. Para mejorar el rendimiento, implementamos técnicas de aumento de datos incluyendo rotación,",
            "escalado y volteo horizontal, lo que resultó en una precisión final del 92.3%."
        ]
    }
]

def get_test_by_id(test_id: int):
    """Get a specific test case by ID"""
    for test in SPANISH_TEST_SUITE:
        if test["id"] == test_id:
            return test
    return None

def get_test_by_name(test_name: str):
    """Get a specific test case by name"""
    for test in SPANISH_TEST_SUITE:
        if test["name"].lower() == test_name.lower():
            return test
    return None

if __name__ == "__main__":
    print("Spanish Test Suite for Chunking Algorithm")
    print("=" * 50)
    for test in SPANISH_TEST_SUITE:
        print(f"\n{test['id']}. {test['name']}")
        print(f"Text: {test['text']}")
        print("Ideal chunks:")
        for i, chunk in enumerate(test['ideal_chunks'], 1):
            print(f"  {i}: {chunk}")