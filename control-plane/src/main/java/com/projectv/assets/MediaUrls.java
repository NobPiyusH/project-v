package com.projectv.assets;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;

import java.net.URI;
import java.time.Duration;

/**
 * Short-lived presigned URLs so the dashboard can play/show media straight from the local S3 store
 * (SeaweedFS supports HTTP range requests, which video seeking needs).
 */
@Component
public class MediaUrls implements AutoCloseable {

    private final S3Presigner presigner;

    public MediaUrls(@Value("${projectv.s3.endpoint}") String endpoint,
                     @Value("${projectv.s3.access-key}") String accessKey,
                     @Value("${projectv.s3.secret-key}") String secretKey) {
        this.presigner = S3Presigner.builder()
                .endpointOverride(URI.create(endpoint))
                .region(Region.US_EAST_1)
                .credentialsProvider(StaticCredentialsProvider.create(AwsBasicCredentials.create(accessKey, secretKey)))
                .serviceConfiguration(S3Configuration.builder().pathStyleAccessEnabled(true).build())
                .build();
    }

    public String presign(String s3Uri, Duration ttl) {
        if (s3Uri == null || !s3Uri.startsWith("s3://")) {
            throw new IllegalArgumentException("Not an s3 uri: " + s3Uri);
        }
        String rest = s3Uri.substring(5);
        int slash = rest.indexOf('/');
        String bucket = rest.substring(0, slash);
        String key = rest.substring(slash + 1);
        return presigner.presignGetObject(GetObjectPresignRequest.builder()
                        .signatureDuration(ttl)
                        .getObjectRequest(GetObjectRequest.builder().bucket(bucket).key(key).build())
                        .build())
                .url().toString();
    }

    @Override
    public void close() {
        presigner.close();
    }
}
