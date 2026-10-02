FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /src
COPY pom.xml .
COPY src src
# The Maven repository lives in a BuildKit cache mount, so dependencies are downloaded
# once and reused by every later build (and by the other reference services).
RUN --mount=type=cache,target=/root/.m2 mvn -q -B -DskipTests package

FROM eclipse-temurin:21-jre
# OpenTelemetry Java auto-instrumentation: traces, metrics and logs are exported over OTLP.
ARG OTEL_AGENT_VERSION=2.10.0
ADD https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar /otel/opentelemetry-javaagent.jar
COPY --from=build /src/target/*.jar /app.jar
ENV JAVA_TOOL_OPTIONS="-javaagent:/otel/opentelemetry-javaagent.jar"
EXPOSE 8080
ENTRYPOINT ["java","-jar","/app.jar"]
