FROM maven:3.9.11-eclipse-temurin-21 AS build
WORKDIR /build
COPY java-services/pom.xml pom.xml
COPY java-services/gateway/pom.xml gateway/pom.xml
COPY java-services/catalog/pom.xml catalog/pom.xml
COPY java-services/catalog/src catalog/src
RUN mvn -B -ntp -pl catalog -am -DskipTests package

FROM eclipse-temurin:21-jre-alpine
RUN addgroup -S app && adduser -S -G app app
WORKDIR /app
COPY --from=build /build/catalog/target/catalog-0.1.0-SNAPSHOT.jar app.jar
USER app
EXPOSE 8081
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
