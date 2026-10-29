// DCU 실습실 모니터링 — Jenkins 멀티브랜치 파이프라인 (aibootcamp 와 같은 방식)
// - main    → 운영    (dcu-agent-monitor,     백엔드 :30085 / 대시보드 :30030)
// - develop → develop (dcu-agent-monitor-dev, 백엔드 :30086 / 대시보드 :30031)
// - 그 외 브랜치·PR → 테스트만 (이미지 푸시·배포 안 함)
// 에이전트(Windows exe)는 Jenkins 가 아니라 build_agent.bat 로 빌드·업로드한다 (README 참고).

pipeline {
    agent any

    options {
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    parameters {
        // 저장소 밖에서 바꾼 설정(Secret 등)을 반영하거나 파드를 새로 띄우고 싶을 때
        booleanParam(name: 'FORCE_RESTART', defaultValue: false, description: '변경이 없어도 백엔드·대시보드를 재시작')
    }

    environment {
        REGISTRY = 'harbor.cu.ac.kr'
        IMAGE_REPO = 'harbor.cu.ac.kr/dcuclassagent'
        // 빌드 로그에 테스트 출력을 그대로 남긴다 (BuildKit 의 접힌 출력 대신).
        // 입력이 지난 빌드와 같으면 BuildKit 캐시로 테스트 단계가 CACHED 로 표시된다 (이미 같은 입력으로 통과한 결과)
        BUILDKIT_PROGRESS = 'plain'
    }

    stages {

        stage('Checkout') {
            steps { checkout scm }
        }

        stage('환경 결정') {
            steps {
                script {
                    if (!env.BRANCH_NAME) {
                        error('멀티브랜치 파이프라인 잡에서만 실행합니다 (BRANCH_NAME 없음 — README 의 Jenkins 잡 설정 참고)')
                    }
                    env.GIT_SHA = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
                    // 배포 여부는 이 한 곳에서만 정한다 (아래 단계들은 env.NAMESPACE 유무로 판단)
                    if (env.BRANCH_NAME == 'main') {
                        env.ENV_LABEL     = '운영'
                        env.NAMESPACE     = 'dcu-agent-monitor'
                        env.CHANNEL_TAG   = 'latest'
                        env.API_URL       = 'http://203.250.35.27:30085'
                        env.WS_URL        = 'ws://203.250.35.27:30085'
                        env.DASHBOARD_URL = 'http://203.250.33.67:30030'
                        // 운영 매니페스트 정본은 Jenkins 에이전트의 파일 (Secret 연결 등 저장소에 없는 설정 포함)
                        env.MANIFEST      = '/services/dcuclassagent/k8s-manifest.yaml'
                    } else if (env.BRANCH_NAME == 'develop') {
                        env.ENV_LABEL     = 'develop'
                        env.NAMESPACE     = 'dcu-agent-monitor-dev'
                        env.CHANNEL_TAG   = 'develop'
                        env.API_URL       = 'http://203.250.35.27:30086'
                        env.WS_URL        = 'ws://203.250.35.27:30086'
                        env.DASHBOARD_URL = 'http://203.250.33.67:30031'
                        env.MANIFEST      = 'server/k8s-manifest-dev.yaml'
                    } else {
                        env.ENV_LABEL     = '테스트만'
                    }
                    if (env.NAMESPACE) {
                        // 불변 태그: 브랜치-빌드번호-커밋 (잡을 다시 만들어 번호가 1부터 시작해도 기존 태그를 덮어쓰지 않음)
                        env.IMAGE_TAG = "${env.BRANCH_NAME}-${env.BUILD_NUMBER}-${env.GIT_SHA}"
                    }
                    echo "[${env.ENV_LABEL}] branch=${env.BRANCH_NAME} commit=${env.GIT_SHA} namespace=${env.NAMESPACE ?: '-'} tag=${env.IMAGE_TAG ?: '-'}"
                }
            }
        }

        // 모든 브랜치·PR: 서버 테스트 + 대시보드 빌드(타입 검사 포함) 확인
        stage('Test') {
            parallel {
                stage('backend') {
                    steps {
                        sh 'docker build --pull -f server/backend/Dockerfile.test -t dcu-backend-test:ci-${BUILD_NUMBER}-${GIT_SHA} server/backend'
                    }
                    post {
                        always { sh 'docker rmi dcu-backend-test:ci-${BUILD_NUMBER}-${GIT_SHA} >/dev/null 2>&1 || true' }
                    }
                }
                stage('dashboard') {
                    // 배포 브랜치는 아래 Build & Push 에서 같은 빌드를 하므로 여기서는 나머지 브랜치만
                    when { expression { return env.NAMESPACE == null } }
                    steps {
                        sh 'docker build --target builder -t dcu-dashboard-test:ci-${BUILD_NUMBER}-${GIT_SHA} server/dcu-dashboard'
                    }
                    post {
                        always { sh 'docker rmi dcu-dashboard-test:ci-${BUILD_NUMBER}-${GIT_SHA} >/dev/null 2>&1 || true' }
                    }
                }
            }
        }

        // 불변 태그만 푸시한다. 환경 태그(latest/develop)는 두 이미지가 모두 빌드된 뒤 Deploy 에서 옮긴다
        // (한쪽 빌드가 실패했는데 다른 쪽 환경 태그만 바뀌는 일이 없도록)
        stage('Build & Push') {
            when { expression { return env.NAMESPACE != null } }
            parallel {
                stage('backend') {
                    steps {
                        script {
                            def img = docker.build("${IMAGE_REPO}/backend:${IMAGE_TAG}", 'server/backend')
                            docker.withRegistry("https://${REGISTRY}", 'harbor') {
                                img.push()
                            }
                        }
                    }
                }
                stage('dashboard') {
                    steps {
                        script {
                            // NEXT_PUBLIC_* 는 빌드 시점에 번들에 들어가므로 환경별 백엔드 주소로 따로 빌드한다
                            def img = docker.build(
                                "${IMAGE_REPO}/frontend:${IMAGE_TAG}",
                                "--build-arg NEXT_PUBLIC_API_URL=${API_URL} --build-arg NEXT_PUBLIC_WS_URL=${WS_URL} server/dcu-dashboard"
                            )
                            docker.withRegistry("https://${REGISTRY}", 'harbor') {
                                img.push()
                            }
                        }
                    }
                }
            }
        }

        stage('Deploy') {
            when { expression { return env.NAMESPACE != null } }
            steps {
                script {
                    docker.withRegistry("https://${REGISTRY}", 'harbor') {
                        // 바뀐 서비스만 재시작한다. 비교 키 = 이미지에 들어가는 소스의 git 해시 + 매니페스트 해시.
                        // 대시보드만 고쳤는데 백엔드가 재시작돼 메모리 상태(DANGER 유지 등)가 초기화되고 보고가 잠깐 끊기는 일을 막고,
                        // 매니페스트(ConfigMap 등)가 바뀌면 두 서비스 모두 재시작해 새 설정을 읽게 한다.
                        // 지난 배포의 키는 Deployment 주석(dcu-monitoring/source-tree)에 기록한다.
                        sh '''
                            set -e
                            manifest_hash=$(sha256sum "$MANIFEST" | cut -c1-12)

                            deploy_service() {
                                name=$1; shift
                                # 이미지 입력 경로들의 git 해시를 하나로 합친다 (테스트·문서만 바뀐 커밋은 재시작하지 않음).
                                # 경로가 없으면 rev-parse 가 실패해 배포를 멈춘다 (파이프로 이으면 실패가 가려지므로 두 단계로 나눔)
                                hashes=$(git rev-parse $(for p in "$@"; do printf 'HEAD:%s ' "$p"; done))
                                src=$(echo "$hashes" | git hash-object --stdin)
                                key="$src-$manifest_hash"
                                current=$(kubectl get "deploy/$name" -n "$NAMESPACE" -o jsonpath='{.metadata.annotations.dcu-monitoring/source-tree}' 2>/dev/null || true)
                                if [ "$key" = "$current" ] && [ "$FORCE_RESTART" != "true" ]; then
                                    echo "[$name] 소스·설정 변경 없음 — 재시작 생략"
                                    return 0
                                fi
                                docker tag "$IMAGE_REPO/$name:$IMAGE_TAG" "$IMAGE_REPO/$name:$CHANNEL_TAG"
                                docker push "$IMAGE_REPO/$name:$CHANNEL_TAG"
                                kubectl rollout restart "deploy/$name" -n "$NAMESPACE"
                                kubectl rollout status "deploy/$name" -n "$NAMESPACE" --timeout=5m
                                kubectl annotate "deploy/$name" -n "$NAMESPACE" "dcu-monitoring/source-tree=$key" --overwrite
                                echo "[$name] 배포 완료 ($IMAGE_TAG, key=$key)"
                            }

                            if [ "$BRANCH_NAME" = "develop" ]; then
                                # 저장소 매니페스트는 develop 네임스페이스로만 적용 (실수로 다른 네임스페이스가 적혀도 운영을 건드리지 않게)
                                # 처음 실행 때 네임스페이스·DB·PVC 도 여기서 생성된다
                                kubectl apply -n "$NAMESPACE" -f "$MANIFEST"
                            else
                                kubectl apply -f "$MANIFEST"
                            fi
                            # DB 가 먼저 뜬 뒤 백엔드를 재시작한다 (첫 배포 때 백엔드가 DB 연결 실패로 반복 재시작하지 않게)
                            kubectl rollout status deploy/postgres -n "$NAMESPACE" --timeout=5m

                            deploy_service backend  server/backend/app server/backend/Dockerfile server/backend/requirements.txt server/backend/constraints.txt
                            deploy_service frontend server/dcu-dashboard
                        '''
                    }
                }
            }
        }
    }

    post {
        success {
            script {
                if (env.NAMESPACE) {
                    echo "[${env.ENV_LABEL}] 배포 완료 — 대시보드 ${env.DASHBOARD_URL} / 백엔드 ${env.API_URL}"
                } else {
                    echo "[${env.ENV_LABEL}] 테스트 통과 (${env.BRANCH_NAME}) — 이미지 푸시·배포는 develop·main 에서만 합니다"
                }
            }
        }
    }
}
