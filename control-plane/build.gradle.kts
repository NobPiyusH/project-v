plugins {
	java
	id("org.springframework.boot") version "4.1.2"
	id("io.spring.dependency-management") version "1.1.7"
}

group = "com.projectv"
version = "0.1.0-SNAPSHOT"

java {
	toolchain {
		languageVersion = JavaLanguageVersion.of(21)
	}
}

repositories {
	mavenCentral()
}

extra["springAiVersion"] = "2.0.2"
extra["temporalVersion"] = "1.40.0"

dependencies {
	implementation("org.springframework.boot:spring-boot-starter-actuator")
	implementation("org.springframework.boot:spring-boot-starter-flyway")
	implementation("org.springframework.boot:spring-boot-starter-jdbc")
	implementation("org.springframework.boot:spring-boot-starter-validation")
	implementation("org.springframework.boot:spring-boot-starter-webmvc")
	implementation("org.flywaydb:flyway-database-postgresql")
	implementation("org.springframework.ai:spring-ai-starter-model-google-genai")
	// Plain Temporal SDK, wired by hand in TemporalConfig: the Temporal Spring Boot starter targets older Boot lines.
	implementation("io.temporal:temporal-sdk:${property("temporalVersion")}")
	implementation("org.yaml:snakeyaml")
	runtimeOnly("org.postgresql:postgresql")

	testImplementation("org.springframework.boot:spring-boot-starter-webmvc-test")
	testImplementation("io.temporal:temporal-testing:${property("temporalVersion")}")
	testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

dependencyManagement {
	imports {
		mavenBom("org.springframework.ai:spring-ai-bom:${property("springAiVersion")}")
	}
}

tasks.withType<Test> {
	useJUnitPlatform()
}

// Run from the repo root so config/ and .env resolve the same way for every module.
tasks.named<org.springframework.boot.gradle.tasks.run.BootRun>("bootRun") {
	workingDir = rootProject.projectDir
	jvmArgs("-Djava.net.preferIPv4Stack=true")
}
