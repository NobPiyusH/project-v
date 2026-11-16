package com.projectv;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

import java.util.TimeZone;

@SpringBootApplication
public class ControlPlaneApplication {

	public static void main(String[] args) {
		// Server runs in UTC. (Windows reports India as the legacy "Asia/Calcutta", which current PostgreSQL
		// rejects; UTC also keeps stored timestamps unambiguous. The dashboard shows local time.)
		TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
		// On the owner's network Java's IPv6 connections to some Google hosts hang (Gradle and Gemini both hit it).
		// Must be set before any socket is opened.
		System.setProperty("java.net.preferIPv4Stack", "true");
		SpringApplication.run(ControlPlaneApplication.class, args);
	}

}
